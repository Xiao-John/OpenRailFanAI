"""12306 sale-page execution fare, bound to a live row's internal train and segment."""
from decimal import Decimal, InvalidOperation
import re

from app.tools import _rt12306 as rt
from app.tools._http import BROWSER_HEADERS, get_client

PRICE_URL='https://kyfw.12306.cn/otn/leftTicket/queryTicketPrice'
INIT='https://kyfw.12306.cn/otn/leftTicket/init'
ROWS='https://kyfw.12306.cn/otn/leftTicket/queryI'
SEATS={'A9':'商务座','P':'特等座','M':'一等座','O':'二等座','A6':'高级软卧','A4':'软卧',
       'F':'动卧','A3':'硬卧','A2':'软座','A1':'硬座','WZ':'无座'}


def amount(value):
    text=str(value).strip()
    if text.startswith(('¥','￥')):text=text[1:]
    if not re.fullmatch(r'\d+(?:\.\d+)?',text):return None
    try:
        number=Decimal(text)
        return format(number,'f') if number.is_finite() and number>=0 else None
    except InvalidOperation:return None


async def query(from_station,to_station,day,train_code):
    if not train_code:raise rt.Realtime12306Error('实际执行票价查询需要具体车次，请补充车次')
    a=await rt.resolve_station_code(from_station);b=await rt.resolve_station_code(to_station)
    if not a or not b:raise rt.Realtime12306Error('无法确认实际乘车区间的车站')
    client=await get_client();headers={**BROWSER_HEADERS,'Referer':INIT}
    response=await rt._operation('fare.init',client.get(INIT,headers=headers,timeout=15),success=lambda r:r.status_code<400)
    response.raise_for_status()
    response=await rt._operation('fare.rows',client.get(ROWS,headers=headers,timeout=15,params={
        'leftTicketDTO.train_date':day,'leftTicketDTO.from_station':a[0],
        'leftTicketDTO.to_station':b[0],'purpose_codes':'ADULT'}),success=lambda r:r.status_code<400)
    response.raise_for_status();payload=response.json();data=payload.get('data')
    if payload.get('status') is not True or not isinstance(data,dict) or not isinstance(data.get('result'),list):
        raise rt.Realtime12306Error('售票查询未返回有效车次记录，无法取得执行票价')
    rows=[]
    for raw in data['result']:
        p=str(raw).split('|')
        if len(p)<36:raise rt.Realtime12306Error("售票记录格式无效，无法可靠查询执行票价")
        if (train_code and p[3].upper()!=train_code.upper()) or p[6]!=a[0] or p[7]!=b[0]:continue
        if not p[2] or not p[16].isdigit() or not p[17].isdigit() or not re.fullmatch(r'[A-Za-z0-9]+',p[35]):
            raise rt.Realtime12306Error('售票记录缺少内部车次、区间站序或席别代码，未查询参考票价替代')
        params={'train_no':p[2],'from_station_no':p[16],'to_station_no':p[17],'seat_types':p[35],'train_date':day}
        response=await rt._operation('fare.execution',client.get(PRICE_URL,headers=headers,timeout=15,params=params),success=lambda r:r.status_code<400)
        response.raise_for_status();price_payload=response.json();prices=price_payload.get('data')
        if price_payload.get('status') is not True or not isinstance(prices,dict) or prices.get('train_no')!=p[2]:
            raise rt.Realtime12306Error('执行票价接口失败或内部车次不一致，未使用公布票价替代')
        normalized={name:amount(prices[key]) for key,name in SEATS.items() if key in prices}
        if not normalized or not any(value is not None for value in normalized.values()):
            raise rt.Realtime12306Error('执行票价接口未返回有效席别金额，未使用公布票价替代')
        rows.append({'train_no':p[2],'train_code':p[3].upper(),'from_station':a[1],'to_station':b[1],
                     'start_time':p[8] or None,'arrive_time':p[9] or None,'duration':p[10] or None,
                     'prices':normalized,'fare_basis':'executed'})
    return {'success':True,'from_station':a[1],'to_station':b[1],'train_date':day,'fare_basis':'executed','data':rows}
