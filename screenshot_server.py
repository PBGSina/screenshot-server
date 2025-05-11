from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont
import os
import logging
from datetime import datetime
import asyncio

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = FastAPI()

API_KEY = os.getenv("API_TOKEN", "your-secret-api-key")
api_key_header = APIKeyHeader(name="X-API-Key")

# ثابت برای فاصله زمانی لاگ
LOG_INTERVAL = 300  # هر 5 دقیقه

async def log_periodically():
    """تسک پس‌زمینه برای ثبت لاگ هر 5 دقیقه"""
    while True:
        logger.info(f"سرور اسکرین‌شات در حال اجرا است - {datetime.utcnow()}")
        await asyncio.sleep(LOG_INTERVAL)

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key")
    return api_key

class ScreenshotRequest(BaseModel):
    symbol: str
    signal: str
    interval: str = "5"
    exchange: str = "BINANCE"

async def take_screenshot(symbol: str, interval: str, exchange: str) -> str:
    output_path = f"/tmp/{symbol}_screenshot.png"
    debug_path = f"/tmp/{symbol}_debug_screenshot.png"
    chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}&interval={interval}&theme=dark"
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                logger.info(f"Navigating to {chart_url}")
                await page.goto(chart_url, timeout=90000, wait_until="domcontentloaded")
                logger.info(f"Page loaded for {symbol}")
                await asyncio.sleep(5)
                await page.screenshot(path=output_path, full_page=True, timeout=60000)
                logger.info(f"اسکرین‌شات برای {symbol} ذخیره شد: {output_path}")
                await browser.close()
            return output_path
        except Exception as e:
            logger.error(f"تلاش {attempt + 1} برای اسکرین‌شات {symbol} ناموفق: {str(e)}")
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page()
                    await page.goto(chart_url, timeout=30000)
                    await page.screenshot(path=debug_path, full_page=True)
                    logger.info(f"اسکرین‌شات دیباگ ذخیره شد: {debug_path}")
                    await browser.close()
            except Exception as debug_e:
                logger.error(f"خطا در گرفتن اسکرین‌شات دیباگ: {str(debug_e)}")
            if attempt == max_retries - 1:
                raise HTTPException(status_code=500, detail=f"Failed to take screenshot after {max_retries} attempts: {str(e)}")
            await asyncio.sleep(5)

def add_arrow_to_image(image_path: str, signal_type: str) -> str:
    try:
        img = Image.open(image_path).convert('RGBA')
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype("arial.ttf", 40)
        except:
            font = ImageFont.load_default()
        
        signal_text = "BUY" if signal_type == "خرید" else "SELL"
        text_width = draw.textlength(signal_text, font=font)
        padding = 10
        box_width = text_width + 2 * padding
        box_height = 50 + 2 * padding
        x_position = (img.width - box_width) // 2
        y_position = img.height - box_height - 20
        
        background_color = (0, 128, 0, 200) if signal_type == "خرید" else (255, 0, 0, 200)
        draw.rectangle(
            [(x_position, y_position), (x_position + box_width, y_position + box_height)],
            fill=background_color
        )
        
        draw.text((x_position + padding, y_position + padding), signal_text, fill='white', font=font)
        
        current_time = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
        draw.text((10, img.height - 30), current_time, fill='white', font=font)
        
        img.save(image_path)
        return image_path
    except Exception as e:
        logger.error(f"خطا در پردازش تصویر: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing image: {str(e)}")

@app.post("/screenshot", response_model=dict)
async def get_screenshot(request: ScreenshotRequest, api_key: str = Security(verify_api_key)):
    try:
        image_path = await take_screenshot(
            symbol=request.symbol,
            interval=request.interval,
            exchange=request.exchange
        )
        
        image_path = add_arrow_to_image(image_path, request.signal)
        
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        os.unlink(image_path)
        
        return {"image": image_data.hex()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.on_event("startup")
async def startup_event():
    """شروع تسک لاگ دوره‌ای هنگام راه‌اندازی سرور"""
    asyncio.create_task(log_periodically())
