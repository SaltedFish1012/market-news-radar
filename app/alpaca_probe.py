"""Run locally: python -m app.alpaca_probe ; tests real API subscription without publishing keys."""
import asyncio,json,os,sys,time
from dotenv import load_dotenv
import websockets
load_dotenv()

async def run():
    key=os.getenv('ALPACA_API_KEY','')
    secret=os.getenv('ALPACA_SECRET_KEY','')
    if not key or not secret:
        print('ERROR: Configure ALPACA_API_KEY and ALPACA_SECRET_KEY in .env')
        return 2
    url=os.getenv('ALPACA_NEWS_URL','wss://stream.data.alpaca.markets/v1beta1/news')
    seconds=int(os.getenv('PROBE_SECONDS','60'))
    status={'connected':False,'authenticated':False,'subscribed':False,'news_received':0}
    try:
        async with websockets.connect(url,additional_headers={'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret},open_timeout=15,ping_interval=20,ping_timeout=20) as ws:
            status['connected']=True
            await ws.send(json.dumps({'action':'subscribe','news':['*']}))
            until=time.monotonic()+seconds
            while time.monotonic()<until:
                try: msg=await asyncio.wait_for(ws.recv(),timeout=max(1,until-time.monotonic()))
                except asyncio.TimeoutError:break
                packet=json.loads(msg)
                for row in (packet if isinstance(packet,list) else [packet]):
                    t=row.get('T')
                    if t=='success':
                        if row.get('msg')=='authenticated':status['authenticated']=True
                        print('STATUS:',row.get('msg'))
                    elif t=='subscription':
                        status['subscribed']='*' in row.get('news',[])
                        print('SUBSCRIPTION:',row.get('news'))
                    elif t=='n':
                        status['news_received']+=1
                        print('NEWS:',row.get('created_at'),str(row.get('headline',''))[:140],row.get('symbols',[]))
                    elif t=='error':
                        print('API ERROR:',row.get('msg'))
                        return 1
    except Exception as e:
        print('CONNECTION ERROR:',type(e).__name__,str(e)[:200]);return 1
    print('RESULT:',status)
    print('No news during the window does not mean subscription failed.' if status['subscribed'] and status['news_received']==0 else '')
    return 0 if status['subscribed'] else 1

if __name__=='__main__':
    sys.exit(asyncio.run(run()))
