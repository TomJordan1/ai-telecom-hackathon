from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from app.core.config import settings
from app.api.routes import router as chat_router
from app.api.whatsapp import router as whatsapp_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="Backend API para Copiloto de Transparencia de Facturación (Lucía)"
)

# Set up CORS for frontend channels
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix=settings.API_V1_STR)
app.include_router(whatsapp_router)

# Serve Web Client
app.mount("/static", StaticFiles(directory="app/static"), name="static")

@app.get("/")
def read_root():
    return RedirectResponse(url="/static/index.html")

@app.get("/health")
def health_check():
    return {"status": "ok", "project": settings.PROJECT_NAME, "version": settings.VERSION}
