# 작업 메모: Instagram 공유 → 기본 저장소 자동 저장

날짜: 2026-05-03

## 작업 내용

모바일 앱에서 Instagram 게시물 공유 버튼 → Picklog 선택 시, `storage_id` 없이 URL + JWT만으로 기본 저장소에 자동 저장되도록 `/instagram/save` 엔드포인트를 수정했다.

### 변경 파일

| 파일 | 변경 내용 |
|------|---------|
| `app/schemas/instagram.py` | `InstagramSaveRequest.storage_id: int` → `int \| None = None` |
| `app/routers/instagram.py` | `Storage` import 추가, `_get_default_storage_id()` 헬퍼 추가, `/save` 엔드포인트 수정 |

### 핵심 로직

```python
def _get_default_storage_id(user_id: int, db: Session) -> int:
    # role='owner' + deleted_at IS NULL + joined_at ASC 첫 번째 저장소
    member = (
        db.query(StorageMember)
        .join(Storage, StorageMember.storage_id == Storage.id)
        .filter(
            StorageMember.user_id == user_id,
            StorageMember.role == "owner",
            Storage.deleted_at.is_(None),
        )
        .order_by(StorageMember.joined_at.asc())
        .first()
    )
```

## 결정 이유 (WHY)

**왜 `is_default` 컬럼을 추가하지 않았나?**
- DB 마이그레이션 불필요 → 배포 위험 없음
- 회원가입 시 항상 "내 저장소"(owner) 하나가 반드시 생성되는 것이 보장됨
- 추후 `is_default` 필드를 추가해도 기존 데이터 정합성 문제 없음 (현재 구조가 사실상 기본값)

**왜 `joined_at ASC` 정렬인가?**
- 사용자가 여러 저장소를 만들었을 경우 최초 생성 저장소(회원가입 시 자동 생성된 것)가 기본 저장소
- `created_at`이 아닌 `joined_at`을 쓰는 이유: StorageMember.joined_at이 Storage.created_at과 동일 트랜잭션에서 생성되므로 실질적으로 동일하나, StorageMember 테이블 자체 컬럼으로 JOIN 없이 정렬 가능해서 더 효율적

**왜 기존 `/save` 엔드포인트를 수정했나 (새 엔드포인트를 만들지 않은 이유)?**
- 기존 클라이언트와 하위 호환: `storage_id` 제공 시 기존 동작 그대로
- 중복 코드 방지: 동일한 크롤링/저장 로직을 두 번 쓸 필요 없음
- API 표면 최소화: 엔드포인트를 늘리면 Swagger 문서와 유지보수 부담이 커짐

## 배운 점

- SQLAlchemy의 `column.is_(None)`은 `IS NULL`로 변환되고, `== None`은 경고가 뜨는 안티패턴 — `is_(None)` 사용이 정석
- 모바일 share intent 흐름에서 백엔드가 해야 할 핵심은 "클라이언트가 알 필요 없는 정보를 서버가 자동 해결해 주는 것" — storage_id가 그 대표 사례
