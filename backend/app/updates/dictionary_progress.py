"""Progress stream for the existing dictionary apply flow; no fabricated overall percent."""
import asyncio
import json
from pathlib import Path
import tempfile
from app.updates import dictionary, github


async def stream(version, target):
    loop=asyncio.get_running_loop()
    queue=asyncio.Queue(maxsize=16)
    closed=False

    def enqueue(value):
        if closed:return
        # Coalesce pending telemetry when the client is slower than the importer.
        if queue.full():queue.get_nowait()
        queue.put_nowait(value)

    def progress(value):
        loop.call_soon_threadsafe(enqueue, {'type':'progress', **value})

    async def work():
        try:
            progress({'stage':'check'})
            info=await dictionary.check(target)
            if info['latest_version']!=version:
                raise github.UpdateError('release_changed','词典版本已变化，请重新检查')
            if not info['update_available']:
                enqueue({'type':'done','component':'dictionary','status':'unchanged','current':info['current']})
                return
            with tempfile.TemporaryDirectory(prefix='railfan-dictionary-') as folder:
                archive=await asyncio.wait_for(github.download(info['asset'],Path(folder),progress=progress),timeout=180)
                snapshot=Path(folder)/'snapshot.db'
                await dictionary.run_io(dictionary.build_feed,archive,snapshot,version,progress)
                progress({'stage':'commit'})
                result=await dictionary.run_io(dictionary.merge,snapshot,target)
            enqueue({'type':'done','component':'dictionary',**result})
        except asyncio.TimeoutError:
            enqueue({'type':'error','code':'timeout','message':'词典下载超时'})
        except github.UpdateError as error:
            enqueue({'type':'error','code':error.code,'message':error.message})
        except asyncio.CancelledError:raise
        except Exception:
            enqueue({'type':'error','code':'internal','message':'词典更新失败，请刷新本地版本确认'})

    task=asyncio.create_task(work())
    try:
        while True:
            try:event=await asyncio.wait_for(queue.get(),timeout=10)
            except asyncio.TimeoutError:
                yield ': heartbeat\n\n'
                continue
            yield 'data: '+json.dumps(event,ensure_ascii=False)+'\n\n'
            if event['type'] in ('done','error'):break
    finally:
        closed=True
        task.cancel()
        # run_io drains local workers before temporary files are removed.
        await asyncio.gather(task,return_exceptions=True)
