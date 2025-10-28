from fastapi import FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from playwright.async_api import async_playwright
from PIL import Image, ImageDraw, ImageFont
import os
import logging
from datetime import datetime
import asyncio
import psutil
import sys
import uuid
import tempfile  # برای path موقتی امن‌تر

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

app = FastAPI()

API_KEY = os.getenv("API_TOKEN", "your-secret-api-key")
api_key_header = APIKeyHeader(name="X-API-Key")

# لیست صرافی‌های پشتیبانی‌شده
SUPPORTED_EXCHANGES = {'BINANCE', 'KUCOIN', 'BYBIT', 'KRAKEN', 'GATEIO'}

# محدود کردن تعداد مرورگرهای هم‌زمان
semaphore = asyncio.Semaphore(2)

# شمارش خطاهای متوالی
consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 5  # حداکثر خطاهای متوالی قبل از ری‌استارت

async def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="کلید API نامعتبر")
    return api_key

class ScreenshotRequest(BaseModel):
    symbol: str
    signal: str
    interval: str = "5"
    exchange: str = "BINANCE"

async def close_playwright_resources(context=None, browser=None):
    """بستن ایمن منابع Playwright"""
    if context and not getattr(context, '_closed', True):
        try:
            await context.close()
            logger.info("Context بسته شد")
        except Exception as e:
            logger.error(f"خطا در بستن context: {str(e)}")
    if browser and not getattr(browser, '_closed', True):
        try:
            await browser.close()
            logger.info("مرورگر بسته شد")
        except Exception as e:
            logger.error(f"خطا در بستن مرورگر: {str(e)}")

async def take_screenshot(symbol: str, interval: str, exchange: str) -> str:
    global consecutive_errors
    if exchange.upper() not in SUPPORTED_EXCHANGES:
        raise HTTPException(status_code=400, detail=f"صرافی {exchange} پشتیبانی نمی‌شود. صرافی‌های موجود: {', '.join(SUPPORTED_EXCHANGES)}")
    
    # استفاده از tempfile برای path امن‌تر (به جای /tmp مستقیم)
    temp_dir = tempfile.gettempdir()
    output_path = os.path.join(temp_dir, f"{symbol}_screenshot_{uuid.uuid4().hex}.png")
    debug_path = os.path.join(temp_dir, f"{symbol}_debug_screenshot_{uuid.uuid4().hex}.png")
    chart_url = f"https://www.tradingview.com/chart/?symbol={exchange}:{symbol}&interval={interval}&theme=dark"
    
    max_retries = 5  # افزایش retry برای cold start Render.com
    browser = None
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--single-process',
                    '--disable-background-networking',
                    '--disable-background-timer-throttling',
                ]
            )
            for attempt in range(max_retries):
                context = None
                try:
                    async with semaphore:
                        memory_info = psutil.virtual_memory()
                        logger.info(f"مصرف حافظه قبل از پردازش {symbol}: {memory_info.percent}% (در دسترس: {memory_info.available / 1024 / 1024:.2f} MB، سماфор گرفته شد)")
                        
                        context = await browser.new_context(
                            viewport={'width': 1280, 'height': 720},
                            no_viewport=True
                        )
                        page = await context.new_page()
                        
                        logger.info(f"رفتن به {chart_url} برای {symbol} (تلاش {attempt + 1})")
                        await page.goto(chart_url, timeout=90000, wait_until="networkidle")  # timeout به 90 ثانیه
                        logger.info(f"صفحه برای {symbol} بارگذاری شد")
                        
                        # انتظار برای رندر کامل صفحه
                        await page.wait_for_load_state("networkidle", timeout=90000)
                        await asyncio.sleep(5)  # صبر اضافی برای لود کامل TradingView
                        
                        await page.screenshot(path=output_path, full_page=True, timeout=90000)
                        logger.info(f"اسکرین‌شات اولیه برای {symbol} ذخیره شد: {output_path}")
                        
                        # چک وجود و سایز فایل بعد از screenshot (کلید fix intermittent issue)
                        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:  # حداقل 1KB برای معتبر بودن
                            logger.info(f"فایل screenshot معتبر است: {output_path} (سایز: {os.path.getsize(output_path)} bytes)")
                            consecutive_errors = 0
                            return output_path
                        else:
                            logger.warning(f"فایل screenshot نامعتبر یا خالی: {output_path} (سایز: {os.path.getsize(output_path) if os.path.exists(output_path) else 0} bytes)")
                            if os.path.exists(output_path):
                                os.remove(output_path)
                            raise ValueError(f"Screenshot file invalid: too small or missing")
                        
                except Exception as e:
                    logger.error(f"تلاش {attempt + 1} برای اسکرین‌شات {symbol} ناموفق: {str(e)}")
                    consecutive_errors += 1
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        logger.error(f"تعداد خطاهای متوالی به {consecutive_errors} رسید. ری‌استارت سرور...")
                        sys.exit(1)
                    
                    if attempt < max_retries - 1:
                        await asyncio.sleep(10)  # صبر بیشتر بین retryها (برای cold start)
                finally:
                    await close_playwright_resources(context, None)
    except Exception as e:
        logger.error(f"خطای بحرانی در پردازش {symbol}: {str(e)}")
        consecutive_errors += 1
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            logger.error(f"تعداد خطاهای متوالی به {consecutive_errors} رسید. ری‌استارت سرور...")
            sys.exit(1)
        raise HTTPException(status_code=500, detail=f"گرفتن اسکرین‌شات برای {symbol} ناموفق بود: {str(e)}")
    finally:
        await close_playwright_resources(None, browser)
        memory_info = psutil.virtual_memory()
        logger.info(f"مصرف حافظه پس از پردازش {symbol}: {memory_info.percent}% (در دسترس: {memory_info.available / 1024 / 1024:.2f} MB)")

def add_arrow_to_image(image_path: str, signal_type: str) -> str:
    # چک اولیه وجود فایل
    if not image_path or not os.path.exists(image_path):
        raise ValueError(f"Image file does not exist or is None: {image_path}")
    
    try:
        logger.info(f"پردازش تصویر: {image_path} (سایز: {os.path.getsize(image_path)} bytes)")
        img = Image.open(image_path).convert('RGBA')
        draw = ImageDraw.Draw(img)
        
        try:
            font = ImageFont.truetype("LiberationSans-Regular.ttf", 40)
        except:
            font = ImageFont.load_default()
            logger.warning("فونت LiberationSans-Regular.ttf یافت نشد، استفاده از فونت پیش‌فرض")
        
        signal_text = "BUY" if signal_type == "خرید" else "SELL"
        # fallback برای textlength (سازگاری با نسخه‌های مختلف PIL)
        try:
            text_width = draw.textlength(signal_text, font=font)
        except AttributeError:
            bbox = draw.textbbox((0, 0), signal_text, font=font)
            text_width = bbox[2] - bbox[0]
        
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
        raise HTTPException(status_code=500, detail=f"خطا در پردازش تصویر: {str(e)}")

@app.get("/ping")
async def ping(api_key: str = Security(verify_api_key)):
    """اندپوینت برای پینگ کردن سرور"""
    logger.info(f"درخواست پینگ دریافت شد - {datetime.utcnow()}")
    return {"status": "alive"}

@app.get("/screenshot/ping")
async def screenshot_ping(api_key: str = Security(verify_api_key)):
    """اندپوینت برای پینگ کردن سرور (برای سازگاری با /screenshot/ping)"""
    logger.info(f"درخواست پینگ اسکرین‌شات دریافت شد - {datetime.utcnow()}")
    return {"status": "alive"}

@app.post("/screenshot", response_model=dict)
async def get_screenshot(request: ScreenshotRequest, api_key: str = Security(verify_api_key)):
    image_path = None
    try:
        image_path = await take_screenshot(
            symbol=request.symbol,
            interval=request.interval,
            exchange=request.exchange
        )
        
        # چک نهایی قبل از پردازش
        if not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
            raise ValueError(f"Screenshot file invalid after creation: {image_path}")
        
        image_path = add_arrow_to_image(image_path, request.signal)
        
        # چک نهایی بعد از پردازش
        if not os.path.exists(image_path):
            raise ValueError("Processed image file does not exist")
        
        with open(image_path, "rb") as f:
            image_data = f.read()
        
        if not image_data:
            raise ValueError("Image data is empty")
        
        logger.info(f"اسکرین‌شات کامل برای {request.symbol} ارسال شد")
        return {"image": image_data.hex()}
    except ValueError as ve:
        logger.error(f"ValueError در اسکرین‌شات: {str(ve)}")
        raise HTTPException(status_code=500, detail=str(ve))
    except Exception as e:
        logger.error(f"خطا در پردازش درخواست اسکرین‌شات برای {request.symbol}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # cleanup در finally برای جلوگیری از leak
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
                logger.info(f"فایل اسکرین‌شات {image_path} حذف شد")
            except Exception as e:
                logger.warning(f"خطا در حذف فایل اسکرین‌شات {image_path}: {str(e)}")