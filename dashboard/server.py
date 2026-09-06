"""
Web dashboard server — FastAPI + uvicorn.

Run standalone:
    python -m dashboard.server

Or launch from main.py via --with-dashboard flag.
"""

from __future__ import annotations

import os
import sys

# Ensure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ccxt's bundled toolz runs `git describe` at import time to detect its version.
# On Windows, git walks up from site-packages and may find an unrelated ~/.git
# (e.g. an empty repo in the home directory), producing a spurious
# "fatal: bad revision 'HEAD'" on stderr.  Capping the search at AppData stops
# git before it reaches the home directory.
if sys.platform == "win32" and not os.environ.get("GIT_CEILING_DIRECTORIES"):
    _appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA", "")
    if _appdata:
        os.environ["GIT_CEILING_DIRECTORIES"] = os.path.dirname(_appdata)

from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import config

from dashboard.auth import DashboardAuthMiddleware
from dashboard.api.market import router as market_router
from dashboard.api.trades import router as trades_router
from dashboard.api.universe import router as universe_router
from dashboard.api.ws import router as ws_router

_STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Trading Bot Dashboard", docs_url=None, redoc_url=None)

SECRET_KEY = os.getenv("SHARED_SSO_SECRET", "sso-shared-secret-key-12345")
ADMIN_SESSION_VAL = "trading-bot-admin-session-ok"

# Ziyaretçi Sayacı
visitors_count = 0

@app.middleware("http")
async def sso_and_visitor_middleware(request: Request, call_next):
    global visitors_count
    path = request.url.path

    # GET / (sayfa yüklemesi) durumunda ziyaretçiyi artır
    if request.method == "GET" and path == "/":
        visitors_count += 1

    # Sağlık kontrolü, SSO ve API rotalarını hariç tut (API rotaları DashboardAuthMiddleware tarafından korunur)
    if path == "/auth/sso" or path == "/health" or path.startswith("/api"):
        response = await call_next(request)
        response.headers["X-Visitor-Count"] = str(visitors_count)
        return response

    # Çerez kontrolü (Dashboard sayfaları ve API rotaları için)
    session_cookie = request.cookies.get("session")
    if session_cookie != ADMIN_SESSION_VAL:
        # Erişim Engellendi HTML sayfasını dön
        denied_html = """
        <!DOCTYPE html>
        <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
                <title>Crypto Trading Bot - Algoritmik Al-Sat Robotu</title>
                <script src="https://cdn.tailwindcss.com"></script>
                <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
                <style>
                    body {
                        font-family: 'Plus Jakarta Sans', sans-serif;
                    }
                    .font-mono {
                        font-family: 'JetBrains Mono', monospace;
                    }
                    .neon-border {
                        box-shadow: 0 0 15px rgba(6, 182, 212, 0.15);
                    }
                    .neon-border:hover {
                        box-shadow: 0 0 25px rgba(6, 182, 212, 0.35);
                    }
                </style>
            </head>
            <body class="bg-[#030712] text-gray-100 min-h-screen flex flex-col justify-between selection:bg-cyan-500/30 selection:text-cyan-200">
                
                <!-- Navbar -->
                <header class="max-w-7xl w-full mx-auto px-6 py-6 flex justify-between items-center border-b border-gray-900">
                    <div class="flex items-center gap-3">
                        <span class="w-10 h-10 bg-cyan-500/10 border border-cyan-500/30 text-cyan-400 rounded-xl flex items-center justify-center font-extrabold text-xl">🤖</span>
                        <span class="text-xl font-bold tracking-tight bg-gradient-to-r from-cyan-400 to-indigo-400 bg-clip-text text-transparent">cryptoTrader</span>
                    </div>
                    <a href="https://consol.erkinlabs.com" class="px-5 py-2.5 bg-cyan-600 hover:bg-cyan-500 text-white text-sm font-semibold rounded-xl transition-all shadow-lg shadow-cyan-900/20 active:scale-95">Konsol Girişi</a>
                </header>

                <!-- Hero Section -->
                <main class="max-w-5xl w-full mx-auto px-6 py-16 text-center">
                    <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-cyan-500/10 border border-cyan-500/20 text-cyan-400 text-xs font-semibold mb-8">
                        <span class="w-2 h-2 rounded-full bg-cyan-500 animate-pulse"></span>
                        7/24 Otomatik Algoritmik Al-Sat
                    </div>
                    
                    <h1 class="text-4xl md:text-6xl font-extrabold tracking-tight mb-6 leading-none">
                        Yapay Zeka Destekli<br />
                        <span class="bg-gradient-to-r from-cyan-400 via-teal-400 to-indigo-400 bg-clip-text text-transparent">Kripto Al-Sat Robotu</span>
                    </h1>
                    
                    <p class="text-gray-400 text-lg md:text-xl max-w-2xl mx-auto mb-10 leading-relaxed">
                        CVD (Cumulative Volume Delta), MACD ve RSI-VWAP indikatörleri ile güçlendirilmiş, Binance ve küresel borsalarda anlık piyasa analizi yapan gelişmiş işlem algoritması.
                    </p>

                    <div class="flex flex-col sm:flex-row justify-center gap-4 mb-20">
                        <a href="https://consol.erkinlabs.com" class="px-8 py-4 bg-gradient-to-r from-cyan-600 to-indigo-600 hover:from-cyan-500 hover:to-indigo-500 text-white font-semibold rounded-2xl transition-all shadow-xl shadow-cyan-900/30 active:scale-95">Robot Panelini Başlat</a>
                        <a href="#features" class="px-8 py-4 bg-gray-900/50 hover:bg-gray-900 border border-gray-800 text-gray-300 hover:text-white font-semibold rounded-2xl transition-all">Özellikleri İncele</a>
                    </div>

                    <!-- Features Grid -->
                    <section id="features" class="py-12 border-t border-gray-900 text-left">
                        <h2 class="text-2xl font-bold mb-10 text-center text-cyan-400">Gelişmiş Algoritma Yetenekleri</h2>
                        <div class="grid md:grid-cols-3 gap-6">
                            
                            <!-- Card 1 -->
                            <div class="bg-gray-900/30 border border-gray-800/80 rounded-2xl p-6 hover:border-cyan-500/30 transition-all neon-border">
                                <span class="text-3xl mb-4 block">🤖</span>
                                <h3 class="text-lg font-semibold mb-2">Çoklu Strateji Modülü</h3>
                                <p class="text-gray-400 text-sm leading-relaxed">Trend takip eden MACD, aşırı alım/satım bölgelerini yakalayan RSI-VWAP ve hacim eğilimlerini izleyen CVD botları ile her piyasa koşuluna uyum sağlar.</p>
                            </div>

                            <!-- Card 2 -->
                            <div class="bg-gray-900/30 border border-gray-800/80 rounded-2xl p-6 hover:border-cyan-500/30 transition-all neon-border">
                                <span class="text-3xl mb-4 block">📈</span>
                                <h3 class="text-lg font-semibold mb-2">Canlı Performans & Defter</h3>
                                <p class="text-gray-400 text-sm leading-relaxed">Cüzdan bakiyesini, anlık P&L durumlarını, açık pozisyonları ve son 10 işlemi gecikmesiz olarak arayüzde canlı grafiklerle takip edin.</p>
                            </div>

                            <!-- Card 3 -->
                            <div class="bg-gray-900/30 border border-gray-800/80 rounded-2xl p-6 hover:border-cyan-500/30 transition-all neon-border">
                                <span class="text-3xl mb-4 block">🛡️</span>
                                <h3 class="text-lg font-semibold mb-2">Risk Kontrol & Stop-Loss</h3>
                                <p class="text-gray-400 text-sm leading-relaxed">Sermayeyi korumak için entegre dinamik kaldıraç kontrolleri, otomatik Stop-Loss ve Take-Profit limit takipleri ile yüksek güvenlikli işlem yönetimi.</p>
                            </div>

                        </div>
                    </section>
                </main>

                <!-- Footer -->
                <footer class="max-w-7xl w-full mx-auto px-6 py-8 border-t border-gray-900 text-center text-xs text-gray-600">
                    <p>© 2026 ErkinLabs. Tüm hakları saklıdır. Bu panel sadece yetkili kullanıcıların erişimine açıktır.</p>
                </footer>

            </body>
        </html>
        """
        response = HTMLResponse(content=denied_html, status_code=403)
        response.headers["X-Visitor-Count"] = str(visitors_count)
        return response

    response = await call_next(request)
    response.headers["X-Visitor-Count"] = str(visitors_count)
    return response

@app.get("/auth/sso")
def sso_login(token: str):
    import hmac
    import hashlib
    import base64
    from datetime import datetime
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        timestamp_str, project, signature = decoded.split(":")
        
        age = int(datetime.utcnow().timestamp() * 1000) - int(timestamp_str)
        if age > 60000:
            raise HTTPException(status_code=401, detail="SSO jetonunun süresi dolmuş.")
            
        secret = SECRET_KEY.encode("utf-8")
        msg = f"{timestamp_str}:{project}".encode("utf-8")
        expected_sig = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        
        if signature != expected_sig:
            raise HTTPException(status_code=401, detail="Geçersiz SSO imzası.")
            
        redirect_resp = RedirectResponse(url="/", status_code=303)
        redirect_resp.set_cookie(
            key="session", 
            value=ADMIN_SESSION_VAL, 
            httponly=True, 
            secure=True, 
            samesite="lax",
            path="/"
        )
        return redirect_resp
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=400, detail=f"SSO doğrulaması başarısız oldu: {str(e)}")

app.add_middleware(DashboardAuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_router, prefix="/api")
app.include_router(trades_router, prefix="/api")
app.include_router(universe_router, prefix="/api")
app.include_router(ws_router)


@app.get("/health")
async def health() -> dict:
    from dashboard.server import get_universe_manager

    mgr = get_universe_manager()
    return {
        "status": "ok",
        "paper_trading": config.PAPER_TRADING,
        "use_dynamic_universe": config.USE_DYNAMIC_UNIVERSE,
        "universe_active_count": config.UNIVERSE_ACTIVE_COUNT,
        "universe_scan_status": mgr.scan_status if mgr else None,
        "universe_active_symbols": len(mgr.active_symbols) if mgr else None,
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


# ── Shared live bot references ────────────────────────────────────────────────
# Populated by main.py when --with-dashboard is active.

_live_bots: list = []
_universe_manager = None


def register_bots(bots: list) -> None:
    global _live_bots
    _live_bots = bots


def register_universe(manager) -> None:
    global _universe_manager
    _universe_manager = manager


def get_live_bots() -> list:
    return _live_bots


def get_universe_manager():
    return _universe_manager


# ── Runner ────────────────────────────────────────────────────────────────────

def run(host: str = "0.0.0.0", port: int = 7000) -> None:
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    run()
