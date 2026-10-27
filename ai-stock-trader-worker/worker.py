"""Paper-only, long-lived stock strategy worker. No live trading endpoints."""
from __future__ import annotations
import json, logging, os, time, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PAPER = 'https://paper-api.alpaca.markets'
DATA = 'https://data.alpaca.markets'
SYMBOLS = [x.strip().upper() for x in os.getenv('SYMBOLS','AMD,NVDA,AAPL,MSFT,TSLA,AMZN,GOOGL,META,TTWO').split(',') if x.strip()]
INTERVAL_SECONDS = 300
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() != 'false'
ORDER_LIMIT = int(os.getenv('MAX_DAILY_ORDERS', '10'))
BUDGET_USD = min(1000.0, float(os.getenv('TRADING_BUDGET_USD', '1000')))
MIN_TRADE_USD = float(os.getenv('MIN_TRADE_USD', '700'))
if not 0 < MIN_TRADE_USD <= BUDGET_USD: raise ValueError('Invalid aggressive trade limits')
# Long-only cash trades. A 1% market-order cushion limits overspending from slippage.
CASH_BUFFER = 0.99
RUN_DIR = os.getenv('LOG_DIR','.')
logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('paper-worker')


def request(path, *, market_data=False, method='GET', payload=None):
    key, secret = os.getenv('ALPACA_PAPER_KEY'), os.getenv('ALPACA_PAPER_SECRET')
    if not key or not secret: raise RuntimeError('Missing ALPACA_PAPER_KEY/ALPACA_PAPER_SECRET')
    url = (DATA if market_data else PAPER) + path
    headers = {'APCA-API-KEY-ID':key,'APCA-API-SECRET-KEY':secret,'Content-Type':'application/json'}
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url,headers=headers,data=body,method=method)
    try:
        with urllib.request.urlopen(req,timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f'API error {exc.code} for {path.split("?")[0]}') from exc


def journal(row):
    os.makedirs(RUN_DIR,exist_ok=True)
    row={'time':datetime.now(timezone.utc).isoformat(), **row}
    with open(os.path.join(RUN_DIR,'trading-events.jsonl'),'a',encoding='utf8') as f:
        f.write(json.dumps(row,ensure_ascii=False)+'\n')
    log.info('%s',row)


def in_regular_session(clock):
    if not clock.get('is_open'):return False
    ny=datetime.now(ZoneInfo('America/New_York'))
    return ny.weekday()<5 and (ny.hour*60+ny.minute)>=570 and (ny.hour*60+ny.minute)<960


def signal(closes):
    """Position direction from last 8 and 21 closes; None if insufficient data."""
    if len(closes)<25:return None
    fast=sum(closes[-8:])/8
    slow=sum(closes[-21:])/21
    if fast>slow*1.0005:return 'long'
    if fast<slow*0.9995:return 'short'
    return 'neutral'


def cycle():
    clock=request('/v2/clock')
    if not in_regular_session(clock):
        journal({'event':'closed','detail':'No orders outside regular NYSE session'})
        return
    account=request('/v2/account')
    if account.get('status')!='ACTIVE' or account.get('trading_blocked'):
        journal({'event':'blocked_account'});return
    equity=float(account['equity']);cash=float(account['cash'])
    if equity<=0:raise RuntimeError('Invalid equity')
    budget_usd = min(equity, BUDGET_USD)
    positions=request('/v2/positions')
    orders=request('/v2/orders?status=all&limit=500&direction=desc')
    day=datetime.now(ZoneInfo('America/New_York')).strftime('%Y%m%d')
    bot_orders=[x for x in orders if str(x.get('client_order_id','')).startswith('cl'+day)]
    if len(bot_orders)>=ORDER_LIMIT:
        journal({'event':'daily_order_limit'});return
    gross=sum(abs(float(x.get('market_value') or 0)) for x in positions)
    # Reserve notional for open orders to prevent overlapping cycles exceeding budget.
    open_states = ('new','accepted','pending_new','partially_filled','held')
    gross += sum(max(budget_usd,
                     abs(float(x.get('notional') or (float(x.get('qty') or 0) * float(x.get('limit_price') or x.get('filled_avg_price') or 0)))))
                 for x in orders if x.get('status') in open_states)
    per_order_cap = budget_usd
    count=0
    for ticker in SYMBOLS:
        if count>=2 or len(bot_orders)+count>=ORDER_LIMIT:break
        if any(x.get('symbol')==ticker and x.get('status') in ('new','accepted','pending_new','partially_filled','held') for x in orders):continue
        if any(x.get('symbol')==ticker for x in bot_orders):continue
        try:
            asset=request('/v2/assets/'+ticker)
            if not asset.get('tradable'):
                journal({'event':'untradable','symbol':ticker});continue
            now=datetime.now(timezone.utc)
            start=(now-timedelta(days=7)).isoformat().replace('+00:00','Z')
            end=(now-timedelta(minutes=20)).isoformat().replace('+00:00','Z')
            from urllib.parse import urlencode
            bars=request('/v2/stocks/'+ticker+'/bars?'+urlencode({'start':start,'end':end,'timeframe':'5Min','feed':'iex','limit':1000}),market_data=True).get('bars',[])
            closes=[float(x['c']) for x in bars if float(x.get('c',0))>0]
            direction=signal(closes)
            holding=next((x for x in positions if x.get('symbol')==ticker),None)
            held=float(holding['qty']) if holding else 0
            if direction is None:
                journal({'event':'insufficient_bars','symbol':ticker});continue
            if held:continue # Phase 1: never auto-close existing positions, preserve ownership boundary.
            if direction=='neutral':continue
            if direction != 'long':continue  # No margin, no shorting.
            available = max(0.0, budget_usd - gross)
            px=closes[-1]
            notional = min(per_order_cap, available, cash) * CASH_BUFFER
            qty=int(notional/px)
            if qty<1 or qty*px < MIN_TRADE_USD:continue
            payload={'symbol':ticker,'qty':str(qty),'side':'buy' if direction=='long' else 'sell',
                     'type':'market','time_in_force':'day', 'client_order_id':'cl'+day+ticker}
            journal({'event':'candidate','symbol':ticker,'direction':direction,'qty':qty,'dry_run':DRY_RUN,'budget_usd':round(budget_usd,2),'minimum_trade_usd':MIN_TRADE_USD})
            if not DRY_RUN:
                order=request('/v2/orders',method='POST',payload=payload)
                journal({'event':'submitted','symbol':ticker,'order_id':order.get('id'),'status':order.get('status')})
            count+=1;gross+=qty*px
            cash-=qty*px
        except Exception as exc:
            journal({'event':'error','symbol':ticker,'message':str(exc)[:160]})


def run():
    if os.getenv('LIVE_TRADING','false').lower()=='true':
        raise RuntimeError('Live trading is forbidden in this worker')
    journal({'event':'started','dry_run':DRY_RUN,'symbols':SYMBOLS,'interval_seconds':INTERVAL_SECONDS})
    while True:
        begin=time.monotonic()
        try:cycle()
        except Exception as exc:journal({'event':'cycle_error','message':str(exc)[:160]})
        time.sleep(max(1,INTERVAL_SECONDS-(time.monotonic()-begin)))

if __name__=='__main__':run()
