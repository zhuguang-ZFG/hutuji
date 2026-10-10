"""按整机SN关联双板、冻结配方和证据；返修重置验收代次，旧记录不删除。"""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import time
import uuid
import shutil
from contextlib import contextmanager
from contextvars import ContextVar

from .core import CHECKS, FactoryError, identifier, mac, digest, digest_json, approved, validate_measurements


class Store:
    def __init__(self,path: Path):
        path.parent.mkdir(parents=True,exist_ok=True)
        self.path=path
        self._operation = ContextVar('factory_operation', default=None)
        with self.connect() as db:
            db.executescript('''
CREATE TABLE IF NOT EXISTS units(sn TEXT PRIMARY KEY,batch TEXT NOT NULL,board TEXT NOT NULL,
 recipe_sha TEXT NOT NULL,manifest_sha TEXT NOT NULL,generation INTEGER NOT NULL DEFAULT 1,
 released INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS identities(mac TEXT PRIMARY KEY,sn TEXT NOT NULL,role TEXT NOT NULL,
 UNIQUE(sn,role),FOREIGN KEY(sn) REFERENCES units(sn));
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,sn TEXT NOT NULL,generation INTEGER NOT NULL,
 check_name TEXT NOT NULL,result TEXT NOT NULL,operator TEXT NOT NULL,evidence TEXT NOT NULL,
 evidence_sha TEXT NOT NULL,detail TEXT NOT NULL,created REAL NOT NULL,
 FOREIGN KEY(sn) REFERENCES units(sn));
CREATE TABLE IF NOT EXISTS operations(sn TEXT PRIMARY KEY,port TEXT UNIQUE NOT NULL,
 token TEXT NOT NULL,operator TEXT NOT NULL,created REAL NOT NULL,
 FOREIGN KEY(sn) REFERENCES units(sn));
CREATE TABLE IF NOT EXISTS event_attachments(event_id TEXT NOT NULL,name TEXT NOT NULL,
 path TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,
 PRIMARY KEY(event_id,name),FOREIGN KEY(event_id) REFERENCES events(id));
''')

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=20)
        db.row_factory=sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:yield db
        finally:db.close()

    def register(self,sn,batch,board,recipe_sha,manifest_sha,s3,grbl,ap):
        identifier(sn);identifier(batch)
        identities=[mac(v) for v in (s3,grbl,ap)]
        if len(set(identities))!=3:raise FactoryError("双板及AP MAC必须互不相同")
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("INSERT INTO units(sn,batch,board,recipe_sha,manifest_sha,created) VALUES(?,?,?,?,?,?)",
                           (sn,batch,board,recipe_sha,manifest_sha,time.time()))
                db.executemany("INSERT INTO identities(mac,sn,role) VALUES(?,?,?)",
                               [(v,sn,k) for k,v in zip(('s3','grbl','ap'),identities)])
        except sqlite3.IntegrityError as exc:raise FactoryError("SN或MAC已登记，禁止串台/覆盖") from exc

    def unit(self,sn):
        with self.connect() as db:
            row=db.execute("SELECT * FROM units WHERE sn=?",(sn,)).fetchone()
            if not row:raise FactoryError("设备未登记")
            value=dict(row)
            value['macs']={r['role']:r['mac'] for r in db.execute("SELECT * FROM identities WHERE sn=?",(sn,))}
            return value

    def _snapshot(self,evidence,event_id):
        if not evidence.is_file():raise FactoryError("证据文件不存在")
        expected=digest(evidence)
        immutable=self.path.parent/(self.path.name+'.evidence')
        immutable.mkdir(exist_ok=True)
        snapshot=immutable/(event_id+evidence.suffix)
        shutil.copyfile(evidence,snapshot)
        if digest(snapshot)!=expected:raise FactoryError('证据在归档时发生变化')
        return snapshot.resolve(),expected

    def _snapshot_attachments(self,attachments):
        saved=[]
        for name,source in (attachments or {}).items():
            identifier(name)
            source=Path(source)
            if not source.is_file():raise FactoryError('附件不存在: '+name)
            sha=digest(source);size=source.stat().st_size
            directory=self.path.parent/(self.path.name+'.evidence')/'attachments'
            directory.mkdir(parents=True,exist_ok=True)
            target=directory/(sha+source.suffix)
            # 同一备份在pending和终态中复用不可变副本，避免每台重复保存整片。
            try:
                with target.open('xb') as output, source.open('rb') as input_file:
                    shutil.copyfileobj(input_file,output)
            except FileExistsError:
                pass
            if target.stat().st_size!=size or digest(target)!=sha:
                raise FactoryError('附件快照损坏或复制期间变化: '+name)
            saved.append({'name':name,'path':str(target.resolve()),'sha256':sha,'size':size})
        return saved

    @staticmethod
    def _insert_attachments(db,event_id,attachments):
        db.executemany('INSERT INTO event_attachments VALUES(?,?,?,?,?)',
                       [(event_id,item['name'],item['path'],item['sha256'],item['size']) for item in attachments])

    def _require_owner(self,db,sn):
        owner=self._operation.get()
        row=db.execute('SELECT token FROM operations WHERE sn=?',(sn,)).fetchone()
        unit=db.execute('SELECT * FROM units WHERE sn=?',(sn,)).fetchone()
        if (not owner or owner['sn']!=sn or not row or row['token']!=owner['token']
                or not unit or unit['released'] or unit['generation']!=owner['generation']):
            raise FactoryError('操作已解除或验收代次变化，拒绝旧结果回写')
        return unit

    def event(self,sn,check,result,operator,evidence:Path,detail:dict,*,attachments=None,expected_generation=None):
        if not operator.strip() or result not in ('pass','fail','pending'):
            raise FactoryError("必须记录操作者和合法结果")
        # 独立人工记录也先占用本台；自动执行器复用自身token，不能借用另一工位的锁。
        if self._operation.get() is None:
            with self.operation(sn,'record:'+sn,operator):
                return self.event(sn,check,result,operator,evidence,detail,attachments=attachments,
                                  expected_generation=expected_generation)
        with self.connect() as db:
            unit=self._require_owner(db,sn)
            if expected_generation is not None and (type(expected_generation) is not int or expected_generation!=unit['generation']):
                raise FactoryError('证据与当前验收代次不符')
        event_id=uuid.uuid4().hex
        snapshot,sha=self._snapshot(evidence,event_id)
        saved=self._snapshot_attachments(attachments)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            unit=self._require_owner(db,sn)
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",(event_id,sn,unit['generation'],check,result,operator,str(snapshot),sha,json.dumps(detail,ensure_ascii=False),time.time()))
            self._insert_attachments(db,event_id,saved)

    def _attachments(self,db,event_id):
        items=[dict(row) for row in db.execute('SELECT name,path,sha256,size FROM event_attachments WHERE event_id=? ORDER BY name',(event_id,))]
        for item in items:item['path']=str(self.evidence_path(item['path']).resolve())
        return items

    def status(self,sn):
        unit=self.unit(sn)
        with self.connect() as db:
            rows=db.execute("SELECT * FROM events WHERE sn=? AND generation=? ORDER BY created,rowid",(sn,unit['generation'])).fetchall()
            latest={r['check_name']:{**dict(r),'attachments':self._attachments(db,r['id'])} for r in rows}
        missing=[]
        for name in CHECKS:
            item=latest.get(name)
            if not item or item['result']!='pass':missing.append(name);continue
            path=self.evidence_path(item['evidence'])
            if not path.is_file() or digest(path)!=item['evidence_sha']:missing.append(name+':evidence')
            if not self._attachments_intact(item['attachments']):
                missing.append(name+':attachments')
        if unit['released']:
            # released保留历史签核；当前报告损坏必须在status/export/batch同时可见。
            release=latest.get('release')
            if not release or release['result']!='pass':
                missing.append('release')
            elif ({a['name'] for a in release['attachments']} !=
                  {'license_review','electrical_review','radio_review'}
                  or not self._attachments_intact(release['attachments'])):
                missing.append('release:attachments')
        return {**unit,'checks':latest,'missing':missing}

    @staticmethod
    def _attachments_intact(attachments):
        try:
            return all(Path(a['path']).is_file() and Path(a['path']).stat().st_size==a['size']
                       and digest(Path(a['path']))==a['sha256'] for a in attachments)
        except OSError:
            # 文件在检查期间消失或不可读，也属于完整性异常。
            return False

    def evidence_path(self,value):
        path=Path(value)
        return path if path.is_absolute() else self.path.parent/path

    def release(self,sn,operator,recipe,delivery):
        # 同一数据库写锁内检查全部证据，防返修/失败回写与放行竞争。
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_no_operation(db,sn)
            from .documents import validate_delivery
            approved(recipe);validate_delivery(delivery)
            result=self.status(sn)
            if result['recipe_sha']!=digest_json(recipe):raise FactoryError('放行配方已变化')
            if not operator.strip() or result['missing']:raise FactoryError("未满足放行条件: "+','.join(result['missing']))
            validate_measurements(result['checks'],recipe)
            release_id=uuid.uuid4().hex
            reports=self._snapshot_attachments({key:Path(delivery[key]['report']) for key in ('license_review','electrical_review','radio_review')})
            detail={'checks':list(CHECKS),'recipe_sha':digest_json(recipe),'delivery_sha':digest_json(delivery),
                    'recipe':recipe,'delivery':delivery}
            db.execute("UPDATE units SET released=1 WHERE sn=? AND generation=?",(sn,result['generation']))
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",(release_id,sn,result['generation'],'release','pass',operator,'','',json.dumps(detail,ensure_ascii=False),time.time()))
            self._insert_attachments(db,release_id,reports)

    def rework(self,sn,operator,reason):
        if not operator.strip() or not reason.strip():raise FactoryError("返修必须填写人和原因")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_no_operation(db,sn)
            row=db.execute("SELECT generation FROM units WHERE sn=?",(sn,)).fetchone()
            if not row:raise FactoryError("设备未登记")
            generation=row[0]+1
            db.execute("UPDATE units SET generation=?,released=0 WHERE sn=?",(generation,sn))
            db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)",(uuid.uuid4().hex,sn,generation,'rework','pending',operator,'','',json.dumps({'reason':reason},ensure_ascii=False),time.time()))

    @staticmethod
    def _require_no_operation(db,sn):
        if db.execute('SELECT 1 FROM operations WHERE sn=?',(sn,)).fetchone():
            raise FactoryError('该台操作仍在执行或中断未确认，禁止返修/放行')

    @contextmanager
    def operation(self,sn,port,operator):
        token=uuid.uuid4().hex
        if not operator.strip():raise FactoryError('必须填写操作人')
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT released,generation FROM units WHERE sn=?',(sn,)).fetchone()
                if not row or row['released']:raise FactoryError('未登记或已放行，须先登记返修')
                db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(sn,port.upper(),token,operator,time.time()))
        except sqlite3.IntegrityError as exc:raise FactoryError('该台或串口正在被另一工位操作') from exc
        context=self._operation.set({'sn':sn,'token':token,'generation':row['generation']})
        try:yield
        finally:
            try:
                with self.connect() as db:db.execute('DELETE FROM operations WHERE sn=? AND token=?',(sn,token))
            finally:self._operation.reset(context)

    def resolve_operation(self,sn,operator,evidence:Path):
        if not operator.strip() or not evidence.is_file():raise FactoryError('须先核对实机停稳并提供证据')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT generation FROM units WHERE sn=?',(sn,)).fetchone()
            if not row:raise FactoryError('设备未登记')
            event_id=uuid.uuid4().hex
            snapshot,sha=self._snapshot(evidence,event_id)
            db.execute('DELETE FROM operations WHERE sn=?',(sn,))
            db.execute('INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?)',(event_id,sn,row[0],'resolve','pending',operator,str(snapshot),sha,'{}',time.time()))

    def backup(self,destination:Path):
        if destination.exists() or destination.resolve()==self.path.resolve():raise FactoryError("备份目标已存在")
        destination.parent.mkdir(parents=True,exist_ok=True)
        target=sqlite3.connect(destination)
        try:
            with self.connect() as source:source.backup(target)
        finally:target.close()

    def history(self,sn):
        result=self.status(sn)
        with self.connect() as db:
            result['history']=[dict(row) for row in db.execute('SELECT * FROM events WHERE sn=? ORDER BY created,rowid',(sn,))]
            for item in result['history']:item['attachments']=self._attachments(db,item['id'])
        for item in result['history']:
            if item['evidence']:item['evidence']=str(self.evidence_path(item['evidence']).resolve())
        return result

    def archive(self,directory:Path):
        """拷贝数据库与每份历史证据，并改写副本路径；原库不动，目标必须全新。"""
        if directory.exists():raise FactoryError('档案目录已存在')
        directory.mkdir(parents=True)
        target=directory/'line.db'
        self.backup(target)
        bundle=Store(target)
        files=directory/'evidence';files.mkdir()
        with bundle.connect() as db:
            # 中断锁随备份保存，恢复后仍需显式核对，不自动解除。
            for row in db.execute("SELECT id,evidence,evidence_sha FROM events WHERE evidence<>''").fetchall():
                source=self.evidence_path(row['evidence'])
                if not source.is_file() or digest(source)!=row['evidence_sha']:
                    raise FactoryError('历史证据缺失或改变，档案不完整: '+row['id'])
                dest=files/(row['id']+source.suffix);shutil.copyfile(source,dest)
                if digest(dest)!=row['evidence_sha']:raise FactoryError('证据复制校验失败')
                db.execute('UPDATE events SET evidence=? WHERE id=?',(dest.relative_to(directory).as_posix(),row['id']))
            for row in db.execute('SELECT * FROM event_attachments').fetchall():
                source=self.evidence_path(row['path'])
                if not source.is_file() or source.stat().st_size!=row['size'] or digest(source)!=row['sha256']:
                    raise FactoryError('历史附件缺失或改变，档案不完整: '+row['name'])
                dest=files/(row['sha256']+source.suffix)
                if not dest.exists():shutil.copyfile(source,dest)
                if dest.stat().st_size!=row['size'] or digest(dest)!=row['sha256']:
                    raise FactoryError('附件复制校验失败')
                db.execute('UPDATE event_attachments SET path=? WHERE event_id=? AND name=?',
                           (dest.relative_to(directory).as_posix(),row['event_id'],row['name']))
        (directory/'archive.json').write_text(json.dumps({'schema':2,'db':'line.db','evidence':'evidence','created':time.time()},indent=2),encoding='utf-8')
