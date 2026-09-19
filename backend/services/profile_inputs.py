"""Exact inputs fenced by retained CV tasks before publishing slow results."""


def embedding_snapshot(profile) -> tuple:
    return profile.cv_text, profile.title, tuple(profile.skills or [])


def autofill_snapshot(profile) -> tuple:
    return (
        embedding_snapshot(profile),
        tuple(profile.languages or []),
        tuple(profile.locations or []),
        profile.experience_years,
        profile.remote_pref,
    )
