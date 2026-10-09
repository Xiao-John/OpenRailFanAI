import {spawn} from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {pathToFileURL} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';
const [source, destination] = process.argv.slice(2);
const profile = await fs.mkdtemp(path.join(os.tmpdir(), 'photo-reference-'));
const browser = spawn('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge', [
  '--headless','--disable-gpu','--no-first-run','--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'
], {stdio:'ignore'});
let socket;
try {
  let port;
  for (let i=0;i<100;i++) {
    try {port=(await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split('\n')[0];break;} catch {await delay(100);}
  }
  if (!port) throw new Error('Edge调试端口未就绪');
  const targets=await (await fetch('http://127.0.0.1:'+port+'/json/list')).json();
  socket=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
  let sequence=0;const pending=new Map();
  socket.addEventListener('message',event=>{const value=JSON.parse(event.data);const item=pending.get(value.id);if(item){pending.delete(value.id);clearTimeout(item.timer);value.error?item.reject(new Error(value.error.message)):item.resolve(value.result);}});
  function call(method,params={}) {return new Promise((resolve,reject)=>{const id=++sequence;const timer=setTimeout(()=>{pending.delete(id);reject(new Error('CDP超时：'+method));},10000);pending.set(id,{resolve,reject,timer});socket.send(JSON.stringify({id,method,params}));});}
  await call('Page.enable');
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
  await call('Page.navigate',{url:pathToFileURL(path.resolve(source)).href});
  await delay(500);
  const size=await call('Runtime.evaluate',{expression:'JSON.stringify({width:innerWidth,height:innerHeight,ready:document.readyState})',returnByValue:true});
  const viewport=JSON.parse(size.result.value);
  if (viewport.width!==390||viewport.height!==844||viewport.ready!=='complete') throw new Error('设计视口不符合390×844');
  const capture=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
  await fs.writeFile(destination,Buffer.from(capture.data,'base64'));
  console.log(JSON.stringify(viewport));
} finally {
  socket?.close();browser.kill('SIGTERM');browser.unref();
  // Edge may keep its updater alive; the captured viewport is verified independently.
}
