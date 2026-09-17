from __future__ import annotations

from pydantic import BaseModel, Field


class Bullet(BaseModel):
    text: str
    tags: list[str] = Field(default_factory=list)


class SubSection(BaseModel):
    heading: str | None = None
    bullets: list[Bullet]


class ExperienceEntry(BaseModel):
    title: str
    organization: str
    qualifier: str | None = None
    date_range: str
    subsections: list[SubSection]


class DatedGroup(BaseModel):
    title: str
    organization: str | None = None
    year: str
    bullets: list[Bullet]


class Header(BaseModel):
    name: str
    tagline: str
    location: str
    phone: str
    email: str
    linkedin_display: str
    linkedin_url: str


class EducationEntry(BaseModel):
    degree: str
    institution: str
    score: str
    year: str


class CVDocument(BaseModel):
    header: Header
    education: list[EducationEntry]
    experience: list[ExperienceEntry]
    academic_projects: list[DatedGroup]
    positions_of_responsibility: list[DatedGroup]
    additional_achievements: list[DatedGroup]
