"""Cliente ligero y nativo para Supabase (Base de datos PostgreSQL + Storage).
No requiere dependencias externas; utiliza la biblioteca estándar de Python (urllib).
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Carga variables del archivo .env asegurando la ruta absoluta a backend-python/.env
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "delux-fotos")


def is_supabase_enabled() -> bool:
    """Retorna True si las credenciales de Supabase están configuradas."""
    return bool(SUPABASE_URL and SUPABASE_KEY)


def _headers(content_type: str = "application/json", prefer: Optional[str] = None) -> Dict[str, str]:
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _request(method: str, path: str, body: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None) -> Any:
    url = f"{SUPABASE_URL}{path}"
    req = urllib.request.Request(url, data=body, headers=headers or _headers(), method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            if not data:
                return None
            try:
                return json.loads(data.decode("utf-8"))
            except Exception:
                return data.decode("utf-8")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Error Supabase ({e.code}): {error_body}") from e


# ==================== PRODUCTOS (Base de Datos) ====================

def list_products() -> List[Dict[str, Any]]:
    return _request("GET", "/rest/v1/products?select=*&order=id.asc") or []


def get_product(product_id: int) -> Optional[Dict[str, Any]]:
    res = _request("GET", f"/rest/v1/products?id=eq.{product_id}&select=*")
    if res and isinstance(res, list) and len(res) > 0:
        return res[0]
    return None


def create_product(data: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(data).encode("utf-8")
    res = _request(
        "POST",
        "/rest/v1/products",
        body=body,
        headers=_headers(prefer="return=representation"),
    )
    if isinstance(res, list) and len(res) > 0:
        return res[0]
    return res


def delete_product(product_id: int) -> None:
    _request("DELETE", f"/rest/v1/products?id=eq.{product_id}")


# ==================== COTIZACIONES (Base de Datos) ====================

def list_quotes() -> List[Dict[str, Any]]:
    return _request("GET", "/rest/v1/quotes?select=*&order=id.desc") or []


def create_quote(data: Dict[str, Any]) -> Dict[str, Any]:
    body = json.dumps(data).encode("utf-8")
    res = _request(
        "POST",
        "/rest/v1/quotes",
        body=body,
        headers=_headers(prefer="return=representation"),
    )
    if isinstance(res, list) and len(res) > 0:
        return res[0]
    return res


# ==================== STORAGE (Fotos en la Nube) ====================

def upload_photo(filename: str, file_bytes: bytes, content_type: str = "image/jpeg") -> str:
    """Sube una foto al bucket de Supabase Storage y retorna su URL pública."""
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    path = f"/storage/v1/object/{SUPABASE_BUCKET}/{filename}"
    _request("POST", path, body=file_bytes, headers=headers)
    return get_public_url(filename)


def delete_photo(filename: str) -> None:
    """Elimina una foto del bucket de Supabase."""
    path = f"/storage/v1/object/{SUPABASE_BUCKET}/{filename}"
    try:
        _request("DELETE", path)
    except Exception:
        pass


def get_public_url(filename: str) -> str:
    """Construye la URL pública directa desde la CDN de Supabase Storage."""
    return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{filename}"
