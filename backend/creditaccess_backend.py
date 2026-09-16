"""
CreditAccess Backend - Complete Single File with Gemini AI Fallback
Run:
    python -m uvicorn creditaccess_backend:app --reload --port 8000
"""

import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    or_
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from dotenv import load_dotenv
from google import genai

load_dotenv()

# ============================================================
# CONFIGURATION
# ============================================================

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./creditaccess.db")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "change-this-admin-key")
MYSCHEME_API_BASE_URL = os.getenv("MYSCHEME_API_BASE_URL", "").strip()
MYSCHEME_API_KEY = os.getenv("MYSCHEME_API_KEY", "").strip()
MYSCHEME_API_SECRET = os.getenv("MYSCHEME_API_SECRET", "").strip()
MYSCHEME_SCHEMES_PATH = os.getenv("MYSCHEME_SCHEMES_PATH", "/schemes").strip()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AQ.Ab8RN6I0llYARpFEJG_hPBKwox9ZVZZbK2_Qu8s-V8-O3hsMsw").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip()

gemini_client = None
if GEMINI_API_KEY:
    try:
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as ex:
        print(f"Gemini Client Init Warning: {ex}")

# ============================================================
# DATABASE SETUP
# ============================================================

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============================================================
# DATABASE MODELS
# ============================================================

class Scheme(Base):
    __tablename__ = "schemes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    short_description = Column(Text, default="")
    full_description = Column(Text, default="")
    category = Column(String(100), default="All")
    purpose = Column(String(100), default="other")
    min_age = Column(Integer, default=18)
    max_age = Column(Integer, default=100)
    max_income = Column(Float, default=999999999)
    max_amount = Column(Float, default=999999999)
    official_url = Column(String(500), default="")
    is_active = Column(Boolean, default=True)

    source_name = Column(String(100), default="manual")
    source_scheme_id = Column(String(255), nullable=True, index=True)
    source_url = Column(String(500), default="")
    source_last_updated = Column(String(100), nullable=True)
    last_synced_at = Column(String(100), nullable=True)
    sync_status = Column(String(50), default="manual")
    verified_by_admin = Column(Boolean, default=False)

class SchemeTranslation(Base):
    __tablename__ = "scheme_translations"

    id = Column(Integer, primary_key=True, index=True)
    scheme_id = Column(Integer, nullable=False, index=True)
    language = Column(String(10), nullable=False, index=True)
    name = Column(String(255), default="")
    short_description = Column(Text, default="")
    full_description = Column(Text, default="")

# ============================================================
# PYDANTIC SCHEMAS
# ============================================================

class SchemeCreate(BaseModel):
    name: str
    short_description: str = ""
    full_description: str = ""
    category: str = "All"
    purpose: str = "other"
    min_age: int = 18
    max_age: int = 100
    max_income: float = 999999999
    max_amount: float = 999999999
    official_url: str = ""

class RecommendationRequest(BaseModel):
    age: int = Field(..., ge=0)
    annual_income: float = Field(..., ge=0)
    category: str
    requirement: str
    required_amount: float = Field(..., ge=0)
    language: str = "en"

class SchemeSearchQuery(BaseModel):
    query: str
    language: str = "en"

# ============================================================
# LANGUAGE SUPPORT
# ============================================================

SUPPORTED_LANGUAGES = {
    "en": "English", "hi": "हिन्दी", "bn": "বাংলা", "mr": "मराठी",
    "ta": "தமிழ்", "te": "తెలుగు", "gu": "ગુજરાતી", "kn": "ಕನ್ನಡ",
    "ml": "മലയാളം", "pa": "ਪੰਜਾਬੀ", "or": "ଓଡ଼ିଆ", "as": "অসমীয়া", "ur": "اردو"
}

def translated_scheme(db: Session, scheme: Scheme, language: str):
    if language == "en":
        return scheme
    translation = (
        db.query(SchemeTranslation)
        .filter(
            SchemeTranslation.scheme_id == scheme.id,
            SchemeTranslation.language == language
        )
        .first()
    )
    if not translation:
        return scheme
    data = {c.name: getattr(scheme, c.name) for c in Scheme.__table__.columns}
    if translation.name:
        data["name"] = translation.name
    if translation.short_description:
        data["short_description"] = translation.short_description
    if translation.full_description:
        data["full_description"] = translation.full_description
    return data

# ============================================================
# SEED INITIAL DATA
# ============================================================

def seed_data(db: Session):
    if db.query(Scheme).count() > 0:
        return
    demo_schemes = [
        Scheme(
            name="PM-SURAJ National Portal",
            short_description="Credit support and assistance for eligible disadvantaged communities.",
            full_description="Credit assistance portal providing direct loans up to ₹10 Lakhs for eligible beneficiaries.",
            category="SC, ST, OBC",
            purpose="business",
            min_age=18,
            max_age=60,
            max_income=300000,
            max_amount=1000000,
            official_url="https://pmsuraj.dosje.gov.in/",
            source_name="demo",
            sync_status="approved",
            is_active=True,
            verified_by_admin=True
        ),
        Scheme(
            name="ASIIM",
            short_description="Ambedkar Social Innovation and Incubation Mission.",
            full_description="Support for entrepreneurship and innovation among SC students and youths with funding up to ₹30 Lakhs.",
            category="SC",
            purpose="startup",
            min_age=18,
            max_age=55,
            max_income=800000,
            max_amount=3000000,
            official_url="https://www.standupmitra.in/",
            source_name="demo",
            sync_status="approved",
            is_active=True,
            verified_by_admin=True
        ),
        Scheme(
            name="Micro Finance Scheme",
            short_description="Small credit assistance for small projects.",
            full_description="Micro credit support for tiny business setups and self-employment initiatives.",
            category="micro",
            purpose="micro",
            min_age=18,
            max_age=65,
            max_income=500000,
            max_amount=140000,
            official_url="https://pmsuraj.dosje.gov.in/",
            source_name="demo",
            sync_status="approved",
            is_active=True,
            verified_by_admin=True
        ),
        Scheme(
            name="Term Loan Scheme",
            short_description="Medium and long term finance for business.",
            full_description="Financial assistance for viable business and industrial ventures.",
            category="term",
            purpose="term",
            min_age=18,
            max_age=60,
            max_income=500000,
            max_amount=1500000,
            official_url="https://pmsuraj.dosje.gov.in/",
            source_name="demo",
            sync_status="approved",
            is_active=True,
            verified_by_admin=True
        ),
        Scheme(
            name="Education Loan Scheme",
            short_description="Concessional education loans for higher studies.",
            full_description="Financial loans for professional and higher education in India and abroad.",
            category="education",
            purpose="education",
            min_age=16,
            max_age=40,
            max_income=500000,
            max_amount=2000000,
            official_url="https://pmsuraj.dosje.gov.in/",
            source_name="demo",
            sync_status="approved",
            is_active=True,
            verified_by_admin=True
        )
    ]
    db.add_all(demo_schemes)
    db.commit()

# ============================================================
# GEMINI AI FALLBACK
# ============================================================

def ask_gemini_fallback(query: str, language: str = "en") -> str:
    if not gemini_client:
        return "Gemini API client not initialized. Check GEMINI_API_KEY."

    lang_name = SUPPORTED_LANGUAGES.get(language, "English")
    prompt = (
        f"You are the official CreditAccess AI Scheme Assistant.\n"
        f"User question: '{query}'\n\n"
        f"Provide a clear and structured answer in {lang_name} covering:\n"
        f"1. Overview & Objective\n"
        f"2. Eligibility Criteria\n"
        f"3. Loan/Assistance Amount & Benefits\n"
        f"4. Required Documents & How to Apply."
    )
    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
        return response.text if response.text else "No response generated by Gemini."
    except Exception as e:
        return f"Gemini error: {str(e)}"

# ============================================================
# RECOMMENDATION LOGIC
# ============================================================

def recommend_schemes(
    db: Session,
    age: int,
    annual_income: float,
    category: str,
    requirement: str,
    required_amount: float,
    language: str = "en"
):
    schemes = db.query(Scheme).filter(Scheme.is_active == True).all()
    results = []

    for scheme in schemes:
        score = 0
        reasons = []
        if scheme.min_age <= age <= scheme.max_age:
            score += 25
            reasons.append("Age matches stored criteria.")
        if annual_income <= scheme.max_income:
            score += 25
            reasons.append("Income matches stored criteria.")
        if category.lower() in scheme.category.lower() or scheme.category.lower() == "all":
            score += 20
            reasons.append("Category matches stored category.")
        if requirement.lower() in (scheme.purpose or "").lower():
            score += 20
            reasons.append("Purpose matches scheme target.")
        if required_amount <= scheme.max_amount:
            score += 10
            reasons.append("Amount is within limits.")

        if score > 0:
            results.append({
                "scheme": translated_scheme(db, scheme, language),
                "match_score": score,
                "reasons": reasons
            })

    results.sort(key=lambda item: item["match_score"], reverse=True)
    return results[:10]

# ============================================================
# FASTAPI APP
# ============================================================

Base.metadata.create_all(bind=engine)

with SessionLocal() as db_session:
    seed_data(db_session)

app = FastAPI(title="CreditAccess API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/")
def root():
    return {"message": "CreditAccess backend is running", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok"}

# ============================================================
# SEARCH ENDPOINT (DB FIRST -> GEMINI FALLBACK)
# ============================================================

@app.post("/api/v1/search", tags=["Smart Search"])
def search_schemes(payload: SchemeSearchQuery, db: Session = Depends(get_db)):
    q = payload.query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Query cannot be empty")

    pattern = f"%{q}%"
    matched_schemes = (
        db.query(Scheme)
        .filter(
            Scheme.is_active == True,
            or_(
                Scheme.name.ilike(pattern),
                Scheme.short_description.ilike(pattern),
                Scheme.category.ilike(pattern),
                Scheme.purpose.ilike(pattern)
            )
        )
        .all()
    )

    if matched_schemes:
        first = translated_scheme(db, matched_schemes[0], payload.language)
        title = getattr(first, "name", first.get("name") if isinstance(first, dict) else "Scheme Found")
        desc = getattr(first, "full_description", first.get("full_description") if isinstance(first, dict) else "")
        url = getattr(first, "official_url", first.get("official_url") if isinstance(first, dict) else "")

        return {
            "source": "database",
            "title": title,
            "answer": f"{desc}\n\nOfficial Portal: {url}" if url else desc,
            "count": len(matched_schemes)
        }

    ai_answer = ask_gemini_fallback(q, payload.language)
    return {
        "source": "gemini_ai",
        "title": f"CreditAccess AI: '{q}'",
        "answer": ai_answer,
        "count": 0
    }

@app.get("/api/v1/schemes", tags=["Schemes"])
def list_schemes(category: Optional[str] = None, purpose: Optional[str] = None, language: str = "en", db: Session = Depends(get_db)):
    query = db.query(Scheme).filter(Scheme.is_active == True)
    if category:
        query = query.filter((Scheme.category.ilike(f"%{category}%")) | (Scheme.category == "All"))
    if purpose:
        query = query.filter(Scheme.purpose.ilike(f"%{purpose}%"))
    return [translated_scheme(db, s, language) for s in query.all()]

@app.get("/api/v1/schemes/{scheme_id}", tags=["Schemes"])
def get_scheme(scheme_id: int, language: str = "en", db: Session = Depends(get_db)):
    scheme = db.query(Scheme).filter(Scheme.id == scheme_id, Scheme.is_active == True).first()
    if not scheme:
        raise HTTPException(status_code=404, detail="Scheme not found")
    return translated_scheme(db, scheme, language)

@app.post("/api/v1/recommendations", tags=["Recommendations"])
def recommendations(payload: RecommendationRequest, db: Session = Depends(get_db)):
    results = recommend_schemes(
        db=db,
        age=payload.age,
        annual_income=payload.annual_income,
        category=payload.category,
        requirement=payload.requirement,
        required_amount=payload.required_amount,
        language=payload.language
    )
    return {
        "results": results,
        "note": "Match scores are estimates based on stored criteria and are not official eligibility decisions."
    }

@app.get("/api/v1/languages", tags=["Languages"])
def languages():
    return [{"code": c, "name": n} for c, n in SUPPORTED_LANGUAGES.items()]