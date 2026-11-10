"""Backend de DE LUX (FastAPI + Supabase / SQLite).

Lo usan dos apps Android distintas (empresa y cliente) con la misma base de datos.
Los nombres de los campos JSON coinciden con los modelos Kotlin del módulo `shared`.
Soporta base de datos y fotos en la nube con Supabase, con fallback local a SQLite.
"""
import os
import secrets
import shutil
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# Carga variables de entorno asegurando la ruta a backend-python/.env
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

from fastapi import APIRouter, Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .db import UPLOAD_DIR, db, init_db
from . import supabase_client as supabase

# Clave que la app de la empresa manda en el header X-API-Key
API_KEY = os.getenv("DELUX_API_KEY", "delux-dev-key")
EXTENSIONES_PERMITIDAS = {"jpg", "jpeg", "png", "webp"}
MAX_FOTO_BYTES = 10 * 1024 * 1024  # 10 MB


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="DE LUX API", lifespan=lifespan)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ---------- Errores en el formato que espera la app: {"error": "..."} ----------

@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError):
    partes = []
    for e in exc.errors():
        campo = ".".join(str(x) for x in e["loc"][1:]) or "datos"
        partes.append(f"{campo}: {e['msg']}")
    return JSONResponse({"error": "; ".join(partes)}, status_code=400)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(401, "API key inválida")


# ---------- Conversión de filas a JSON (camelCase, igual que en Kotlin) ----------

def product_json(row) -> dict:
    foto = row["foto"]
    # Si la foto ya es una URL completa en la nube (Supabase), se conserva intacta
    if foto and (foto.startswith("http://") or foto.startswith("https://")):
        foto_url = foto
    elif foto:
        foto_url = f"/uploads/{foto}"
    else:
        foto_url = None

    return {
        "id": row["id"],
        "nombre": row["nombre"],
        "descripcion": row["descripcion"],
        "fotoUrl": foto_url,
        "largoCm": row["largo_cm"],
        "anchoCm": row["ancho_cm"],
        "gruesoCm": row["grueso_cm"],
        "precioM2": row["precio_m2"],
        "creadoEn": row["creado_en"],
    }


def quote_json(row) -> dict:
    return {
        "id": row["id"],
        "productId": row["product_id"],
        "productoNombre": row["producto_nombre"],
        "clienteNombre": row["cliente_nombre"],
        "clienteContacto": row["cliente_contacto"],
        "metrosCuadrados": row["metros_cuadrados"],
        "precioM2": row["precio_m2"],
        "total": row["total"],
        "mensaje": row["mensaje"],
        "estado": row["estado"],
        "creadoEn": row["creado_en"],
    }


def ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


api = APIRouter(prefix="/api")


# ---------- Catálogo (público: lo ve el cliente) ----------

@api.get("/products")
def listar_productos():
    if supabase.is_supabase_enabled():
        rows = supabase.list_products()
        return [product_json(r) for r in rows]

    with db() as conn:
        rows = conn.execute("SELECT * FROM products ORDER BY id").fetchall()
    return [product_json(r) for r in rows]


@api.get("/products/{product_id}")
def obtener_producto(product_id: int):
    if supabase.is_supabase_enabled():
        row = supabase.get_product(product_id)
        if row is None:
            raise HTTPException(404, "Producto no encontrado")
        return product_json(row)

    with db() as conn:
        row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Producto no encontrado")
    return product_json(row)


# ---------- Registro de mercancía (solo la empresa) ----------

@api.post("/products", status_code=201, dependencies=[Depends(require_api_key)])
def registrar_producto(
    nombre: str = Form(...),
    descripcion: str = Form(""),
    largoCm: float = Form(...),
    anchoCm: float = Form(...),
    gruesoCm: float = Form(...),
    precioM2: float = Form(...),
    foto: UploadFile | None = File(None),
):
    if not nombre.strip():
        raise HTTPException(400, "El nombre es obligatorio")
    for etiqueta, valor in (("largoCm", largoCm), ("anchoCm", anchoCm),
                            ("gruesoCm", gruesoCm), ("precioM2", precioM2)):
        if valor <= 0:
            raise HTTPException(400, f"'{etiqueta}' debe ser mayor a 0")

    nombre_foto = None
    if foto is not None and foto.filename:
        ext = Path(foto.filename).suffix.lstrip(".").lower() or "jpg"
        if ext not in EXTENSIONES_PERMITIDAS:
            raise HTTPException(400, "Formato de imagen no permitido (usa jpg, png o webp)")
        contenido = foto.file.read(MAX_FOTO_BYTES + 1)
        if len(contenido) > MAX_FOTO_BYTES:
            raise HTTPException(413, "La imagen pesa más de 10 MB")

        file_uuid = f"{uuid.uuid4()}.{ext}"
        if supabase.is_supabase_enabled():
            mime_type = "image/png" if ext == "png" else ("image/webp" if ext == "webp" else "image/jpeg")
            # Sube la foto directamente a Supabase Storage en la nube y obtiene su URL pública
            nombre_foto = supabase.upload_photo(file_uuid, contenido, content_type=mime_type)
        else:
            nombre_foto = file_uuid
            (UPLOAD_DIR / nombre_foto).write_bytes(contenido)

    if supabase.is_supabase_enabled():
        nuevo = supabase.create_product({
            "nombre": nombre.strip(),
            "descripcion": descripcion.strip(),
            "foto": nombre_foto,
            "largo_cm": largoCm,
            "ancho_cm": anchoCm,
            "grueso_cm": gruesoCm,
            "precio_m2": precioM2,
            "creado_en": ahora(),
        })
        return product_json(nuevo)

    with db() as conn:
        cur = conn.execute(
            """INSERT INTO products
               (nombre, descripcion, foto, largo_cm, ancho_cm, grueso_cm, precio_m2, creado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (nombre.strip(), descripcion.strip(), nombre_foto,
             largoCm, anchoCm, gruesoCm, precioM2, ahora()),
        )
        row = conn.execute("SELECT * FROM products WHERE id = ?", (cur.lastrowid,)).fetchone()
    return product_json(row)


@api.delete("/products/{product_id}", status_code=204, dependencies=[Depends(require_api_key)])
def eliminar_producto(product_id: int):
    if supabase.is_supabase_enabled():
        prod = supabase.get_product(product_id)
        if prod is None:
            raise HTTPException(404, "Producto no encontrado")
        supabase.delete_product(product_id)
        if prod.get("foto") and "/" in prod["foto"]:
            # Borra la foto del bucket de Supabase si corresponde
            foto_filename = prod["foto"].split("/")[-1].split("?")[0]
            supabase.delete_photo(foto_filename)
        return Response(status_code=204)

    with db() as conn:
        row = conn.execute("SELECT foto FROM products WHERE id = ?", (product_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Producto no encontrado")
        conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
    if row["foto"]:
        (UPLOAD_DIR / row["foto"]).unlink(missing_ok=True)
    return Response(status_code=204)


# ---------- Cotizaciones ----------

class QuoteRequest(BaseModel):
    productId: int
    clienteNombre: str
    clienteContacto: str
    metrosCuadrados: float
    mensaje: str = ""


@api.post("/quotes", status_code=201)
def solicitar_cotizacion(req: QuoteRequest):
    """El cliente solicita una cotización (no requiere API key)."""
    if not req.clienteNombre.strip() or not req.clienteContacto.strip():
        raise HTTPException(400, "Nombre y contacto son obligatorios")
    if req.metrosCuadrados <= 0:
        raise HTTPException(400, "Los metros cuadrados deben ser mayores a 0")

    if supabase.is_supabase_enabled():
        producto = supabase.get_product(req.productId)
        if producto is None:
            raise HTTPException(404, "Producto no encontrado")
        precio = float(producto["precio_m2"])
        total = round(precio * req.metrosCuadrados, 2)
        nueva_cotizacion = supabase.create_quote({
            "product_id": req.productId,
            "producto_nombre": producto["nombre"],
            "cliente_nombre": req.clienteNombre.strip(),
            "cliente_contacto": req.clienteContacto.strip(),
            "metros_cuadrados": req.metrosCuadrados,
            "precio_m2": precio,
            "total": total,
            "mensaje": req.mensaje,
            "estado": "PENDIENTE",
            "creado_en": ahora(),
        })
        return quote_json(nueva_cotizacion)

    with db() as conn:
        producto = conn.execute(
            "SELECT * FROM products WHERE id = ?", (req.productId,)
        ).fetchone()
        if producto is None:
            raise HTTPException(404, "Producto no encontrado")

        # El precio sale del servidor, nunca de lo que mande el cliente
        precio = producto["precio_m2"]
        total = round(precio * req.metrosCuadrados, 2)

        cur = conn.execute(
            """INSERT INTO quotes
               (product_id, producto_nombre, cliente_nombre, cliente_contacto,
                metros_cuadrados, precio_m2, total, mensaje, creado_en)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (req.productId, producto["nombre"], req.clienteNombre.strip(),
             req.clienteContacto.strip(), req.metrosCuadrados, precio, total,
             req.mensaje, ahora()),
        )
        row = conn.execute("SELECT * FROM quotes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return quote_json(row)


@api.get("/quotes", dependencies=[Depends(require_api_key)])
def listar_cotizaciones():
    """La empresa revisa las cotizaciones recibidas (más nuevas primero)."""
    if supabase.is_supabase_enabled():
        rows = supabase.list_quotes()
        return [quote_json(r) for r in rows]

    with db() as conn:
        rows = conn.execute("SELECT * FROM quotes ORDER BY id DESC").fetchall()
    return [quote_json(r) for r in rows]


app.include_router(api)
