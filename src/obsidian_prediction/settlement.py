"""Settlement reconciliation and durable Telegram delivery for the paper account."""
import asyncio
import os
import httpx
from .paper_account import settle, state

async def reconcile(client, db, fx, *, concurrency=8):
    rows=db.execute("SELECT id,market_id FROM baskets WHERE status='OPEN_HEDGED'").fetchall()
    sem=asyncio.Semaphore(concurrency)
    async def check(row):
        async with sem:
            try:
                market=await client.market(row[1])
                with db:
                    return int(settle(db,row[0],market,fx))
            except Exception as exc:
                print(f"Settlement check failed for {row[1]}: {type(exc).__name__}: {exc}")
                return 0
    settled=sum(await asyncio.gather(*(check(row) for row in rows)))
    if rows:
        print(f"Settlement reconciliation: checked={len(rows)} settled={settled} equity=A${state(db)['equity']:.2f}")
    return settled

async def deliver(db):
    token=os.environ.get("TELEGRAM_BOT_TOKEN")
    chat=os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        return 0
    sent=0
    async with httpx.AsyncClient(timeout=15) as http:
        rows=db.execute("SELECT basket_id,message FROM telegram_outbox WHERE delivered_at IS NULL").fetchall()
        for basket_id,message in rows:
            try:
                response=await http.post(f"https://api.telegram.org/bot{token}/sendMessage",
                                         json={"chat_id":chat,"text":message})
                response.raise_for_status()
                if not response.json().get("ok"):
                    raise RuntimeError("Telegram API did not acknowledge delivery")
                with db:
                    db.execute("""UPDATE telegram_outbox SET delivered_at=datetime('now'),
                                  attempts=attempts+1,last_error=NULL WHERE basket_id=?""",(basket_id,))
                sent+=1
            except Exception as exc:
                with db:
                    db.execute("""UPDATE telegram_outbox SET attempts=attempts+1,last_error=?
                                  WHERE basket_id=?""",(str(exc)[:300],basket_id))
                print(f"Telegram delivery failed for {basket_id}: {type(exc).__name__}")
    return sent
