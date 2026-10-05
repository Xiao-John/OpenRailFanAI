"""导入固定版本的开放 SVG，并无损转换几何为 Android VectorDrawable。"""
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import urllib.request, xml.etree.ElementTree as ET, json, re
ROOT=Path(__file__).resolve().parents[2]
MAPPING={'calendar':'calendar','clock':'clock','link':'link','info':'info','chevron':'chevron-right','chevron-down':'chevron-down','chevron-up':'chevron-up','chevron-left':'chevron-left','search':'search','train-search':'train-front','person':'user-round','cloud-error':'cloud-alert','file':'file-text','plus':'plus','back':'arrow-left','down':'arrow-down','up':'arrow-up','edit':'square-pen','trash':'trash-2','history':'history','settings':'settings','help':'circle-help','close':'x','stop':'square','send':'send','check':'circle-check','warning':'circle-alert','copy':'copy','refresh':'rotate-cw','more':'ellipsis','share':'share-2'}
OUT=ROOT/'android/app/src/main/res'
A='http://schemas.android.com/apk/res/android'
def path(e):
 t=e.tag.split('}')[-1]; a=e.attrib
 if t=='path': return a['d']
 if t=='line': return f"M{a['x1']},{a['y1']} L{a['x2']},{a['y2']}"
 if t in ('polyline','polygon'):
  values=re.split(r'[\s,]+',a['points'].strip())
  if len(values)%2: raise ValueError(a['points'])
  return 'M'+' L'.join(','.join(values[i:i+2]) for i in range(0,len(values),2))+(' Z' if t=='polygon' else '')
 if t in ('circle','ellipse'):
  x,y=float(a['cx']),float(a['cy']); rx=float(a.get('r',a.get('rx',0))); ry=float(a.get('r',a.get('ry',0)))
  return f'M{x-rx},{y} a{rx},{ry} 0 1,0 {2*rx},0 a{rx},{ry} 0 1,0 {-2*rx},0'
 if t=='rect':
  x,y,w,h=[float(a.get(k,0)) for k in ('x','y','width','height')]; r=float(a.get('rx',0))
  return f'M{x+r},{y} H{x+w-r} Q{x+w},{y} {x+w},{y+r} V{y+h-r} Q{x+w},{y+h} {x+w-r},{y+h} H{x+r} Q{x},{y+h} {x},{y+h-r} V{y+r} Q{x},{y} {x+r},{y} Z'
 raise ValueError(t)
def fetch(item):
 name,slug=item; url=f'https://cdn.jsdelivr.net/npm/lucide-static@0.468.0/icons/{slug}.svg'
 svg=urllib.request.urlopen(url,timeout=30).read()
 (OUT/'raw'/f'lucide_{name.replace("-","_")}.svg').write_bytes(svg)
 root=ET.Element('vector',{'xmlns:android':A,'android:width':'24dp','android:height':'24dp','android:viewportWidth':'24','android:viewportHeight':'24'})
 for e in ET.fromstring(svg):
  ET.SubElement(root,'path',{'android:pathData':path(e),'android:fillColor':'#FF000000' if name=='stop' else '@android:color/transparent','android:strokeColor':'#FF000000','android:strokeWidth':'2','android:strokeLineCap':'round','android:strokeLineJoin':'round'})
 ET.indent(root)
 (OUT/'drawable'/f'ic_{name.replace("-","_")}_lucide.xml').write_text(ET.tostring(root,encoding='unicode')+'\n')
 return {'name':name,'icon':slug,'version':'0.468.0','source':url}
if __name__=='__main__':
 with ThreadPoolExecutor(max_workers=6) as pool: sources=list(pool.map(fetch,MAPPING.items()))
 (ROOT/'third_party/licenses/lucide-icon-sources.json').write_text(json.dumps(sources,indent=2)+'\n')
 print(f'已导入 {len(sources)} 个图标')
