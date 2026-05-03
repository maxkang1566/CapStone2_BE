import httpx
from fastapi import HTTPException

KAKAO_USER_INFO_URL = "https://kapi.kakao.com/v2/user/me"


async def fetch_kakao_user(access_token: str) -> dict:
    """
    카카오 access_token으로 사용자 정보를 조회합니다.

    반환 형식 (카카오 v2/user/me 응답):
        {
            "id": 1234567890,
            "kakao_account": {
                "email": "user@example.com" | None,
                "profile": {
                    "nickname": "닉네임",
                    "profile_image_url": "https://..."
                }
            }
        }

    실패 시:
        - 401: 유효하지 않은 토큰
        - 502: 카카오 서버 오류 또는 네트워크 실패
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(KAKAO_USER_INFO_URL, headers=headers)
    except httpx.RequestError:
        raise HTTPException(status_code=502, detail="카카오 서버에 연결할 수 없습니다.")

    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="유효하지 않은 카카오 토큰입니다.")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail="카카오 사용자 정보 조회에 실패했습니다.")

    return resp.json()
