"""원격 이미지를 Supabase Storage에 영구 저장.

용도별 namespace:
- `instagram/{shortcode}/...` — 인스타 CDN URL은 `oe=` 만료 토큰이 박혀 있어
  4~5일 후 무효(403). AI팀 멀티모달 분석과 운영 사용자 재방문에 안정적으로
  보여주려면 우리 측 영구 호스팅이 필요.
- `naver/{naver_place_id}/...` — 앱 내 네이버지도 검색으로 저장된 장소의
  대표 이미지. 클라이언트가 보내준 image_url을 우리 측에 영구화하여 같은
  이유(DNA 분석에 안정 입력 보장 + 외부 URL 변경/만료 대응).

실패 시 None을 반환해 호출부가 원본 URL로 폴백할 수 있게 한다
(데이터 무결성 우선이 아니라 가용성 우선 — AI팀이 며칠은 원본 URL로
볼 수 있고, 그동안 운영자가 알람 받고 조치 가능).
"""
from __future__ import annotations

import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse

import httpx
from supabase import Client, create_client

logger = logging.getLogger(__name__)

_BUCKET = os.environ.get("SUPABASE_PLACE_IMAGES_BUCKET", "place-images")
_FETCH_TIMEOUT = 10.0
_MAX_BYTES = 10 * 1024 * 1024  # 10MB 안전 상한

_client: Optional[Client] = None


def _get_client() -> Optional[Client]:
    """싱글톤 Supabase 클라이언트. 키 없으면 None을 반환해 호출부가 폴백할 수 있게 한다."""
    global _client
    if _client is not None:
        return _client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        logger.warning(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY 미설정 — image storage 비활성"
        )
        return None
    _client = create_client(url, key)
    return _client


def _build_storage_path(namespace: str, key: Optional[str], original_url: str) -> str:
    """버킷 내 경로. namespace/key 단위로 폴더링해 같은 외부 ID 재크롤 시 덮어쓰기 가능.

    key가 None이면 `{namespace}/_unknown/` 아래로 떨어진다.
    """
    parsed = urlparse(original_url)
    name = os.path.basename(parsed.path) or "image.jpg"
    # 슬래시·제어문자 제거
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    if key:
        # key 자체도 sanitize — 네이버 place_id가 안전한 형태라고 가정하지만 방어적으로
        safe_key = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        return f"{namespace}/{safe_key}/{name}"
    return f"{namespace}/_unknown/{name}"


def _content_type_for(url: str) -> str:
    lower = url.lower().split("?")[0]
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _upload_to_supabase(
    *,
    image_url: str,
    namespace: str,
    key: Optional[str],
) -> Optional[str]:
    """공용 fetch + 검증 + 업로드 + public URL 반환 코어.

    실패 시(None 반환):
    - SUPABASE_URL/KEY 미설정
    - 원본 URL fetch 실패(만료·네트워크)
    - 응답이 이미지가 아님 / 너무 큼
    - 업로드 실패
    """
    client = _get_client()
    if client is None:
        return None

    try:
        resp = httpx.get(image_url, timeout=_FETCH_TIMEOUT, follow_redirects=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "image fetch 실패: %s — %s", e.__class__.__name__, image_url[:80]
        )
        return None
    if resp.status_code != 200:
        logger.warning(
            "image fetch 비정상 응답 %s: %s", resp.status_code, image_url[:80]
        )
        return None

    content = resp.content
    if not content or len(content) > _MAX_BYTES:
        logger.warning(
            "image size 이상(%s bytes): %s",
            len(content) if content else 0, image_url[:80],
        )
        return None

    resp_ct = resp.headers.get("content-type", "").split(";")[0].strip()
    content_type = resp_ct if resp_ct.startswith("image/") else _content_type_for(image_url)

    path = _build_storage_path(namespace, key, image_url)
    try:
        # supabase-py 2.x: storage.from_(bucket).upload(path, file, file_options=...)
        # upsert는 string("true"/"false")로 전달.
        client.storage.from_(_BUCKET).upload(
            path=path,
            file=content,
            file_options={
                "content-type": content_type,
                "upsert": "true",
            },
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Storage upload 실패: %s — path=%s", e, path)
        return None

    try:
        public_url = client.storage.from_(_BUCKET).get_public_url(path)
    except Exception as e:  # noqa: BLE001
        logger.warning("public URL 조회 실패: %s — path=%s", e, path)
        return None

    # supabase-py가 trailing '?' 또는 query를 붙일 수 있어 strip.
    return public_url.rstrip("?")


def upload_instagram_image(
    *,
    image_url: str,
    shortcode: Optional[str] = None,
) -> Optional[str]:
    """인스타 CDN 이미지를 Storage에 업로드하고 public URL을 반환."""
    return _upload_to_supabase(
        image_url=image_url,
        namespace="instagram",
        key=shortcode,
    )


def upload_naver_place_image(
    *,
    image_url: str,
    naver_place_id: str,
) -> Optional[str]:
    """앱 내 네이버지도 저장 흐름의 대표 이미지를 Storage에 업로드하고 public URL을 반환."""
    return _upload_to_supabase(
        image_url=image_url,
        namespace="naver",
        key=naver_place_id,
    )
