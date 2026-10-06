"""Local simulation budget manager. Python standard library only."""
import json, sqlite3, os, time, threading, random
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent
DB = os.environ.get('BUDGET_DB', str(ROOT / 'budget.db'))
CONFIGS = [dict(id='cpu', name='CPU · Balanced', cores=32, gpus=0, rate=1.60, speed=1), dict(id='gpu', name='GPU · Accelerated', cores=16, gpus=1, rate=3.20, speed=3), dict(id='hpc', name='HPC · High memory', cores=64, gpus=0, rate=4.80, speed=2)]
def connect():
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    return c

def init():
    with connect() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS budgets(project_id INTEGER PRIMARY KEY REFERENCES projects(id), amount REAL NOT NULL CHECK(amount>0));
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), user_id INTEGER NOT NULL REFERENCES users(id), name TEXT NOT NULL, config TEXT NOT NULL, work_hours REAL NOT NULL, deadline REAL NOT NULL, estimated REAL NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL, started TEXT, finished TEXT, runtime REAL, gpu_util REAL, fail INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS costs(job_id INTEGER PRIMARY KEY REFERENCES jobs(id), amount REAL NOT NULL, cpu_hours REAL NOT NULL, gpu_hours REAL NOT NULL);
''')
        if not c.execute('SELECT 1 FROM users').fetchone():
            c.execute("INSERT INTO users VALUES(1, 'Alex Morgan')")
            for i,n,d,b in [(1,'Aerodynamics','Wind tunnel & CFD simulations',2400),(2,'Battery research','Thermal modeling & material studies',1600),(3,'Structural analysis','Finite element & load testing',1000)]:
                c.execute('INSERT INTO projects VALUES(?,?,?)',(i,n,d)); c.execute('INSERT INTO budgets VALUES(?,?)',(i,b))
            for i in range(1,19):
                cfg=CONFIGS[i%3]; hours=40+i*3; runtime=hours/cfg['speed']; failed=i in (5,13)
                c.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(i,(i%3)+1,1,['Wing pressure study','Thermal cycle analysis','Chassis load test'][i%3]+' '+str(i),cfg['id'],hours,100,hours/cfg['speed']*cfg['rate'],'failed' if failed else 'succeeded','2026-10-%02dT10:00:00Z'%(1+i%5),None,'2026-10-%02dT12:00:00Z'%(1+i%5),runtime,76 if cfg['gpus'] else None,int(failed)))
                c.execute('INSERT INTO costs VALUES(?,?,?,?)',(i,round(runtime*cfg['rate']*(.4 if failed else 1),2),runtime*cfg['cores'],runtime*cfg['gpus']))
        # Interrupted local demo runs cannot silently retain reservations.
        c.execute("UPDATE jobs SET status='failed', finished=? WHERE status='running'",(now(),))

def now(): return datetime.now(timezone.utc).isoformat()
def config(key): return next(x for x in CONFIGS if x['id']==key)
def available(c,pid):
    budget=c.execute('SELECT amount FROM budgets WHERE project_id=?',(pid,)).fetchone()[0]
    spent=c.execute('SELECT COALESCE(SUM(amount),0) FROM costs JOIN jobs ON jobs.id=costs.job_id WHERE project_id=?',(pid,)).fetchone()[0]
    reserved=c.execute("SELECT COALESCE(SUM(estimated),0) FROM jobs WHERE project_id=? AND status IN ('approved','running')",(pid,)).fetchone()[0]
    return budget-spent-reserved

def snapshot():
    with connect() as c:
        projects=[dict(r) for r in c.execute('SELECT p.*,b.amount budget,COALESCE(SUM(c.amount),0) spent FROM projects p JOIN budgets b ON b.project_id=p.id LEFT JOIN jobs j ON j.project_id=p.id LEFT JOIN costs c ON c.job_id=j.id GROUP BY p.id')]
        for p in projects: p['available']=available(c,p['id']); p['reserved']=round(p['budget']-p['spent']-p['available'],2)
        jobs=[dict(r) for r in c.execute('SELECT j.*,p.name project,u.name owner,c.amount actual,c.cpu_hours,c.gpu_hours FROM jobs j JOIN projects p ON p.id=j.project_id JOIN users u ON u.id=j.user_id LEFT JOIN costs c ON c.job_id=j.id ORDER BY j.id DESC')]
    return dict(projects=projects,jobs=jobs,configs=CONFIGS)

def run_job(jid):
    start=time.monotonic(); time.sleep(5)
    with connect() as c:
        j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone(); cfg=config(j['config'])
        runtime=j['work_hours']/cfg['speed']*(.45 if j['fail'] else random.uniform(.85,1.15))
        c.execute('INSERT INTO costs VALUES(?,?,?,?)',(jid,round(runtime*cfg['rate'],2),runtime*cfg['cores'],runtime*cfg['gpus']))
        c.execute('UPDATE jobs SET status=?,finished=?,runtime=?,gpu_util=? WHERE id=?',('failed' if j['fail'] else 'succeeded',now(),runtime,random.randint(65,96) if cfg['gpus'] else None,jid))

def mutate(data):
    action=data.get('action')
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if action=='submit':
            name=str(data.get('name','')).strip()
            hours=float(data['hours']); deadline=float(data['deadline']); pid=int(data['project']); cfg=config(data['config'])
            if not name or len(name)>120 or not 0<hours<=10000 or not 0<deadline<=10000: raise ValueError('Enter a name and hours between 0 and 10,000.')
            if not c.execute('SELECT 1 FROM projects WHERE id=?',(pid,)).fetchone(): raise ValueError('Unknown project.')
            estimate=round(hours/cfg['speed']*cfg['rate'],2)
            c.execute('INSERT INTO jobs(project_id,user_id,name,config,work_hours,deadline,estimated,status,created,fail) VALUES(?,1,?,?,?,?,?,?,?,?)',(pid,name,cfg['id'],hours,deadline,estimate,'pending',now(),int(bool(data.get('fail')))))
        else:
            jid=int(data['id']); j=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not j: raise ValueError('Job not found.')
            if action in ('approve','reject'):
                if j['status']!='pending': raise ValueError('Only pending jobs can be reviewed.')
                if action=='approve' and j['estimated']>available(c,j['project_id'])+1e-8: raise ValueError('Insufficient available budget. Reject this job or submit a smaller configuration.')
                c.execute('UPDATE jobs SET status=? WHERE id=?',('approved' if action=='approve' else 'rejected',jid))
            elif action=='run':
                if j['status']!='approved': raise ValueError('Approve the job before running it.')
                c.execute("UPDATE jobs SET status='running',started=? WHERE id=?",(now(),jid))
            else: raise ValueError('Unknown action.')
    if action=='run': threading.Thread(target=run_job,args=(jid,),daemon=True).start()

class Handler(BaseHTTPRequestHandler):
    def send(self,code,body,ctype='application/json'):
        self.send_response(code); self.send_header('Content-Type',ctype); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path=='/api/dashboard': return self.send(200,json.dumps(snapshot()).encode())
        path={'/':'index.html','/style.css':'style.css','/app.js':'app.js'}.get(self.path)
        if not path: return self.send(404,b'Not found','text/plain')
        self.send(200,(ROOT/'static'/path).read_bytes(),{'index.html':'text/html','style.css':'text/css','app.js':'text/javascript'}[path])
    def do_POST(self):
        if self.path!='/api/jobs': return self.send(404,b'{}')
        try:
            size=int(self.headers.get('Content-Length',0))
            if not 0<size<10000: raise ValueError('Invalid request size.')
            mutate(json.loads(self.rfile.read(size))); self.send(200,b'{"ok":true}')
        except (ValueError,KeyError,TypeError,StopIteration,sqlite3.IntegrityError) as e: self.send(400,json.dumps({'error':str(e) or 'Invalid configuration.'}).encode())
if __name__=='__main__':
    init(); print('Simulation Budget Manager: http://localhost:8000',flush=True)
    ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('PORT',8000))),Handler).serve_forever()
