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

# لیست صرافی‌های پشتیبانی‌شده در TradingView
SUPPORTED_EXCHANGES = {'BINANCE', 'KUCOIN', 'BYBIT', 'KRAKEN', 'GATEIO'}

# محدود کردن تعداد مرورگرهای هم‌زمان
semaphore = asyncio.Semaphore(2)

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
    if exchange.upper() not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=400, detail=f"صرافی {exchange} پشتیبانی نمی‌شود. صرافی‌های موجود: {', '.join(SUPPORTED_EXCHANGES)}")
    
    output_path = f"/tmp/{symbol}_screenshot.png"
    debug_path = f"/tmp/{symbol}_debug_screenshot.png"
    chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}&interval={interval}&theme=dark"
    
    max_retries = 3
    for attempt in range(max_retries):
        browser = None
        try:
            async with semaphore:  # محدود کردن تعداد مرورگرهای هم‌زمان
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True,
                        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
                    )
                    page = await browser.new_page()
                    logger.info(f"Navigating to {chart_url} for {symbol} (Attempt {attempt + 1})")
                    await page.goto(chart_url, timeout=120000, wait_until="domcontentloaded")
                    logger.info(f"Page loaded for {symbol}")
                    await asyncio.sleep(5)  # صبر برای رندر کامل چارت
                    await page.screenshot(path=output_path, full_page=True, timeout=90000)
                    logger.info(f"اسکرین‌شات برای {symbol} ذخیره شد: {output_path}")
                    return output_path
        except Exception as e:
            logger.error(f"تلاش {attempt + 1} برای اسکرین‌شات {symbol} ناموفق: {str(e)}")
            try:
                async with semaphore:
                    async with async_playwright() as p:
                        browser = await p.chromium.launch(
                            headless=True,
                            args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage']
                        )
                        page = await browser.new_page()
                        await page.goto(chart_url, timeout=30000, wait_until="domcontentloaded")
                        await page.screenshot(path=debug_path, full_page=True, timeout=30000)
                        logger.info(f"اسکرین‌شات دیباگ ذخیره شد: {debug_path}")
            except Exception as debug_e:
                logger.error(f"خطا در گرفتن اسکرین‌شات دیباگ برای {symbol}: {str(debug_e)}")
            if attempt == max_retries - 1:
                raise HTTPException(status_code=500, detail=f"Failed to take screenshot for {symbol} after {max_retries} attempts: {str(e)}")
            await asyncio.sleep(5)
        finally:
            if browser:
                try:
                    await browser.close()
                    logger.info(f"مرورگر برای {symbol} بسته شد")
                except Exception as e:
                    logger.error(f"خطا در بستن مرورگر برای {symbol}: {str(e)}")

def add_arrow_to_image(image_path: str, signal_type: str) -> str:
    try:
        img = Image.open(image_path).convert('RGBA')
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype("LiberationSans-Regular.ttf", 40)
        except:
            font = ImageFont.load_default()
            logger.warning("فونت LiberationSans-Regular.ttf یافت نشد، استفاده از فونت پیش‌فرض")
        
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
        logger.info(f"تصویر برای {signal_type} پردازش شد: {image_path}")
        return image_path
    except Exception as e:
        logger.error(f"خطا در پردازش تصویر: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error processing image: {str(e)}")

@app.get("/ping")
async def ping(api_key: str = Security(verify_api_key)):
    """Endpoint برای پینگ کردن سرور"""
    logger.info(f"درخواست پینگ دریافت شد - {datetime.utcnow()}")
    return {"status": "alive"}

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
        
        try:
            os.unlink(image_path)
            logger.info(f"فایل اسکرین‌شات {image_path} حذف شد")
        except Exception as e:
            logger.warning(f"خطا در حذف فایل اسکرین‌شات {image_path}: {str(e)}")
        
        return {"image": image_data.hex()}
    except Exception as e:
        logger.error(f"خطا در پردازش درخواست اسکرین‌شات برای {request.symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
