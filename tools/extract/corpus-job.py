"""Single resumable corpus job: direct conversion, bounded CUDA OCR, publication."""
import fcntl,json,os,signal,subprocess,sys,time
from pathlib import Path
from common import write_json
ROOT=Path(__file__).resolve().parents[2]
def main():
    config=json.loads((ROOT/'config.local.json').read_text());runtime=Path(config['runtimeRoot']);runtime.mkdir(parents=True,exist_ok=True)
    lock=(runtime/'corpus-job.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    node=runtime/'tools/node/node_modules/node/bin/node'
    python=config.get('pythonExecutable',sys.executable)
    status={'state':'running','phase':'native-conversion','pid':os.getpid(),'startedAt':time.strftime('%Y-%m-%dT%H:%M:%S%z')};child=None
    def refresh():status['updatedAt']=time.strftime('%Y-%m-%dT%H:%M:%S%z');write_json(runtime/'corpus-status.json',status)
    def stop(signum,_frame):
        if child and child.poll() is None:child.terminate()
        raise KeyboardInterrupt(f'Signal {signum}')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    def run(command):
        nonlocal child
        child=subprocess.Popen(command,cwd=ROOT);code=child.wait();child=None
        if code:raise RuntimeError('Corpus stage exited with code '+str(code))
    def publish():run([str(node),'tools/extract/publish-transcripts.mjs','--completed-only'])
    def finalize():
        status['phase']='final-publication';refresh();run([str(node),'tools/extract/publish-transcripts.mjs'])
        status['phase']='audit';refresh();run([python,'-u','tools/extract/audit-corpus.py'])
        status.update(state='complete',phase='complete');refresh()
    try:
        if '--publish-only' in sys.argv:
            finalize();return
        refresh();run([python,'-u','tools/extract/documents.py'])
        status['phase']='publishing-native';refresh();run([python,'-u','tools/extract/resources.py']);publish()
        status['phase']='gpu-ocr';refresh()
        child=subprocess.Popen([python,'-u','tools/extract/convert.py'],cwd=ROOT)
        last_ready=None
        while child.poll() is None:
            time.sleep(10)
            ready=[]
            for source in json.loads((ROOT/'catalog/sources.json').read_text()):
                manifest=runtime/source['output']/'manifest.json'
                if manifest.exists() and len(json.loads(manifest.read_text()).get('pages',[]))==source['pages']:ready.append(source['id'])
            if ready!=last_ready:
                # Publication is bounded and runs in parallel only with the ONE GPU worker.
                subprocess.run([str(node),'tools/extract/publish-transcripts.mjs','--completed-only'],cwd=ROOT,check=True)
                last_ready=ready
        code=child.returncode;child=None
        if code:raise RuntimeError('GPU OCR stopped; see source/page in ocr-status.json')
        finalize()
    except BaseException as error:
        if child and child.poll() is None:child.terminate();child.wait()
        status.update(state='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',error=str(error));refresh();raise
if __name__=='__main__':main()
