"""20-case real system run. No DB, no simulation, no production changes."""
import sys,json,random,time,hashlib,shutil,traceback,argparse,asyncio
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'eval')]
from evaluate_front_half_20261108 import Recorded,read,write
from ict.config import llm_settings,concurrency_settings,parse_settings
from ict.pipeline import announcement_chain
from ict.scheduling import MergeScheduler
from ict.parse.client import ParseClient
OUT=ROOT/'work/real20-20261110'
def main():
 global OUT
 parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=OUT);parser.add_argument('--ids-manifest',type=Path);opts=parser.parse_args();OUT=opts.out
 OUT.mkdir(exist_ok=True)
 old=read(ROOT/'work/evaluation-front-half-20261108/manifest.json')
 summary=read(ROOT/'work/attachment-fieldscope-review-20261109/summary.json')
 pool=[a for a in old['ids'] if a in summary['by_case'] and summary['by_case'][a]['source_units']>1]
 scan=[a for a in pool if summary['by_case'][a]['expected_reading'].get('ocr',0)]
 ids=sorted(scan+random.Random(20261110).sample([a for a in pool if a not in scan],20-len(scan)))
 if opts.ids_manifest:ids=read(opts.ids_manifest)['ids'];assert len(ids)==20
 cfg=llm_settings()
 files=list((ROOT/'src/ict').rglob('*.py'))+list((ROOT/'src/ict/prompts').rglob('*.md'))+list((ROOT/'src/ict/prompts/archive').glob('*.json'))
 hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
 manifest={'ids':ids,'selection':'All 9 reviewed OCR cases plus seeded 11 of remaining readable reviewed cases; seed20261110; diagnostic sample','html':{a:old['html'][a] for a in ids},'model':cfg['model'],'endpoint':cfg['base_url'],'source_sha256':hashes,'start_time':time.strftime('%Y-%m-%d %H:%M:%S'),'no_database':True,'real_ocr':True,'concurrency':concurrency_settings(),'scheduling':'merge_suspend'}
 if (OUT/'manifest.json').exists():
  saved=read(OUT/'manifest.json');assert saved['ids']==ids and saved['source_sha256']==hashes
 else:
  write(OUT/'manifest.json',manifest)
  for p in files:
   q=OUT/'source_snapshot'/p.relative_to(ROOT);q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q)
 s=parse_settings();print(json.dumps({'health':ParseClient(s['url'],token=s['token']).health(),'model':cfg['model'],'cases':ids},ensure_ascii=False),flush=True)
 async def run(a,scheduler):
  dest=OUT/'runs'/a
  if (dest/'10_run_report.json').exists():return {'id':a,'cached_completed':True}
  t=time.perf_counter()
  try:
   report=await scheduler.run(announcement_chain(a,llm=Recorded(cfg,OUT/'calls'/a),html_dir=Path(old['html'][a]).parent,attachments_root=ROOT/'work/attachments',runs_root=OUT/'runs'))
   return {'id':a,'seconds':round(report.duration_ms/1000,1),'status':report.status,'counts':report.counts,'failures':report.failure_summary}
  except Exception as e:
   error={'id':a,'seconds':round(time.perf_counter()-t,1),'error':str(e).replace(cfg['api_key'],'[REDACTED]'),'traceback':traceback.format_exc().replace(cfg['api_key'],'[REDACTED]')}
   write(OUT/'errors'/f'{a}.json',error);return error
 completed=[]
 async def batch():
  settings=concurrency_settings()
  with MergeScheduler(settings['announcement_workers'],settings['merge_workers'],settings['resume_workers']) as scheduler:
   tasks=[asyncio.create_task(run(a,scheduler)) for a in ids]
   for f in asyncio.as_completed(tasks):
    result=await f;completed.append(result);write(OUT/'progress.json',completed);print(json.dumps({'completed':len(completed),**result},ensure_ascii=False),flush=True)
 asyncio.run(batch())
 write(OUT/'integrity.json',{'source_unchanged':all(hashlib.sha256((ROOT/n).read_bytes()).hexdigest()==h for n,h in hashes.items()),'finished':len(completed),'end_time':time.strftime('%Y-%m-%d %H:%M:%S')})
if __name__=='__main__':main()
