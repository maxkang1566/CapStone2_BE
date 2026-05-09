from __future__ import annotations

from datetime import datetime
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    password: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    kakao_id: Mapped[Optional[str]] = mapped_column(String, unique=True, nullable=True)
    nickname: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    profile_image: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    storage_members: Mapped[list[StorageMember]] = relationship(
        "StorageMember", back_populates="user", cascade="all, delete-orphan"
    )
    space_dna: Mapped[Optional[UserSpaceDNA]] = relationship(
        "UserSpaceDNA", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    dna_history: Mapped[list[UserSpaceDNAHistory]] = relationship(
        "UserSpaceDNAHistory", back_populates="user", cascade="all, delete-orphan"
    )


class Storage(Base):
    __tablename__ = "storages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    members: Mapped[list[StorageMember]] = relationship(
        "StorageMember", back_populates="storage", cascade="all, delete-orphan"
    )
    spots: Mapped[list[Spot]] = relationship(
        "Spot", back_populates="storage", cascade="all, delete-orphan"
    )


class StorageMember(Base):
    __tablename__ = "storage_members"

    storage_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("storages.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    # owner(생성자), editor(공동편집자), viewer(구경꾼)
    role: Mapped[str] = mapped_column(String, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("storage_id", "user_id", name="uq_storage_members_storage_user"),
        PrimaryKeyConstraint("storage_id", "user_id"),
    )

    storage: Mapped[Storage] = relationship("Storage", back_populates="members")
    user: Mapped[User] = relationship("User", back_populates="storage_members")


class Place(Base):
    __tablename__ = "places"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    coordinate: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326), nullable=True
    )
    category_group: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    homepage_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    # GeoAlchemy2가 coordinate 컬럼에 idx_places_coordinate GIST 인덱스를 자동 생성합니다.

    spots: Mapped[list[Spot]] = relationship("Spot", back_populates="place")
    # delete-orphan을 쓰지 않는 이유: place_raw_data.place_id가 nullable이라 raw 행이
    # place와 association이 없는 상태(place_id=NULL)로 정상 존재할 수 있다. delete-orphan을
    # 켜두면 ORM이 컬렉션에서 detach된 raw를 즉시 DELETE하는데, 이는 1차 적재(raw 먼저,
    # 정제 나중) 흐름과 충돌한다. place 삭제 시 raw 정리는 DB FK의 ON DELETE CASCADE에 맡긴다.
    raw_data: Mapped[list[PlaceRawData]] = relationship(
        "PlaceRawData", back_populates="place", cascade="save-update, merge"
    )
    images: Mapped[list[PlaceImage]] = relationship(
        "PlaceImage", back_populates="place", cascade="all, delete-orphan"
    )
    reviews: Mapped[list[PlaceReview]] = relationship(
        "PlaceReview", back_populates="place", cascade="all, delete-orphan"
    )
    space_dna: Mapped[Optional[PlaceSpaceDNA]] = relationship(
        "PlaceSpaceDNA", back_populates="place", uselist=False, cascade="all, delete-orphan"
    )
    tags: Mapped[list[PlaceTag]] = relationship(
        "PlaceTag", back_populates="place", cascade="all, delete-orphan"
    )


class PlaceRawData(Base):
    __tablename__ = "place_raw_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # place_id가 nullable인 이유: 인스타 공유 흐름에서 Apify가 게시물을 먼저 긁어
    # 1차 raw로 적재하는 시점에는 아직 어느 Place에 연결될지 모른다(네이버 검색 매핑 후 결정).
    # 정제 단계(spot_creator)에서 place_id를 UPDATE로 채워 raw → places 흐름을 잇는다.
    place_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("places.id", ondelete="CASCADE"), nullable=True
    )
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    provider_place_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "uq_place_raw_data_provider_pid",
            "provider", "provider_place_id",
            unique=True,
            postgresql_where=sa_text("provider_place_id IS NOT NULL"),
        ),
    )

    place: Mapped[Optional[Place]] = relationship("Place", back_populates="raw_data")
    images: Mapped[list[PlaceImage]] = relationship("PlaceImage", back_populates="raw_data")
    reviews: Mapped[list[PlaceReview]] = relationship("PlaceReview", back_populates="raw_data")


class Spot(Base):
    __tablename__ = "spots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    storage_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("storages.id", ondelete="CASCADE"), nullable=False
    )
    place_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    added_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=False
    )
    instagram_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 인스타 공유 흐름에서 Apify로 긁은 caption을 그대로 저장. raw_payload에서도 읽을 수
    # 있지만 사용자 응답마다 JSONB 파싱하기보다 정제 컬럼으로 노출하는 게 단순하고 빠르다.
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_visited: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visited_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    __table_args__ = (
        UniqueConstraint("storage_id", "place_id", name="uq_spots_storage_place"),
    )

    storage: Mapped[Storage] = relationship("Storage", back_populates="spots")
    place: Mapped[Place] = relationship("Place", back_populates="spots")


class PlaceImage(Base):
    __tablename__ = "place_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    place_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    raw_data_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("place_raw_data.id", ondelete="SET NULL"), nullable=True
    )
    uploaded_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    image_url: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    is_representative: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    place: Mapped[Place] = relationship("Place", back_populates="images")
    raw_data: Mapped[Optional[PlaceRawData]] = relationship("PlaceRawData", back_populates="images")


class PlaceSpaceDNA(Base):
    __tablename__ = "place_space_dna"

    place_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("places.id", ondelete="CASCADE"), primary_key=True
    )
    mbti_axes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    place: Mapped[Place] = relationship("Place", back_populates="space_dna")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    place_tags: Mapped[list[PlaceTag]] = relationship(
        "PlaceTag", back_populates="tag", cascade="all, delete-orphan"
    )


class PlaceTag(Base):
    __tablename__ = "place_tags"

    place_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    tag_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        PrimaryKeyConstraint("place_id", "tag_id"),
    )

    place: Mapped[Place] = relationship("Place", back_populates="tags")
    tag: Mapped[Tag] = relationship("Tag", back_populates="place_tags")


class UserSpaceDNA(Base):
    __tablename__ = "user_space_dna"

    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    mbti_axes: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    preferred_vibe_tags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    total_visits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_analyzed: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship("User", back_populates="space_dna")


class UserSpaceDNAHistory(Base):
    __tablename__ = "user_space_dna_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    spot_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("spots.id", ondelete="SET NULL"), unique=True, nullable=True
    )
    mbti_axes_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship("User", back_populates="dna_history")


class InstagramCrawlJob(Base):
    """비동기 크롤링/공유 작업 상태/결과. 클라이언트가 폴링으로 진행 상황을 확인한다.

    `kind`로 작업 종류 구분:
      - 'crawl': /instagram/crawl-async — payload에 InstagramCrawlResponse 직렬화
      - 'share': /instagram/share — payload에 InstagramShareResponse 직렬화,
                 user_id/storage_id로 워커가 share_post를 재구성할 컨텍스트 전달
    """
    __tablename__ = "instagram_crawl_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # UUID 문자열
    kind: Mapped[str] = mapped_column(
        String, nullable=False, server_default="crawl"
    )  # 'crawl' | 'share'
    url: Mapped[str] = mapped_column(String, nullable=False)
    shortcode: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)  # 'pending' | 'done' | 'failed'
    source: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # 'apify' | 'og_fallback' | None
    # share 잡 한정: 워커가 share_post를 재구성할 인증/저장소 컨텍스트
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    storage_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("storages.id", ondelete="SET NULL"), nullable=True
    )
    payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)

    __table_args__ = (
        Index("ix_instagram_crawl_jobs_created_at", "created_at"),
        Index("ix_instagram_crawl_jobs_source_created", "source", "created_at"),
        Index("ix_instagram_crawl_jobs_kind_created", "kind", "created_at"),
    )


class PlaceReview(Base):
    __tablename__ = "place_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    place_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("places.id", ondelete="CASCADE"), nullable=False
    )
    raw_data_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("place_raw_data.id", ondelete="SET NULL"), nullable=True
    )
    provider: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    external_review_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)
    collected_at: Mapped[datetime] = mapped_column(server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_place_reviews_place_id", "place_id"),
        # external_review_id가 NOT NULL인 행에 대해서만 (place_id, provider, external_review_id) 중복 방지
        Index(
            "uq_place_reviews_place_provider_ext_id",
            "place_id", "provider", "external_review_id",
            unique=True,
            postgresql_where=sa_text("external_review_id IS NOT NULL"),
        ),
    )

    place: Mapped[Place] = relationship("Place", back_populates="reviews")
    raw_data: Mapped[Optional[PlaceRawData]] = relationship("PlaceRawData", back_populates="reviews")
