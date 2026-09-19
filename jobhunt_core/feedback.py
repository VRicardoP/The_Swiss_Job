"""Explicit feedback mutation shared by vacancy and school-observation APIs.

Caller holds the profile lock. Related marks change in the same transaction,
without taking a corpus or monitor lock in the opposite order of ingestion.
"""

import sqlalchemy as sa


def effective_feedback_sql(vacancy, profile):
    """Latest explicit intent, including clears and observations linked later.

    Arguments are internal SQL expressions, NEVER request values. Evaluation,
    draft edits and implicit events do not advance this clock. Existing marks
    without a clock remain valid but precede newly recorded user intent.
    """
    return f"""SELECT intent.feedback FROM (
        SELECT CASE WHEN _fs.dismissed_at IS NOT NULL THEN 'dismissed'
                    WHEN _fs.feedback IN ('thumbs_up','applied') THEN _fs.feedback
                    ELSE NULL END AS feedback,
               COALESCE(GREATEST(_fs.feedback_recorded_at,_fs.dismissed_at),
                        '-infinity'::timestamptz) AS stamp, 0 AS priority
        FROM profile_vacancy_state _fs
        WHERE _fs.profile_id={profile} AND _fs.vacancy_id={vacancy}
        UNION ALL
        SELECT _fa.feedback,COALESCE(_fa.feedback_recorded_at,'-infinity'::timestamptz),1
        FROM school_applications _fa JOIN school_job_details _fj ON _fj.id=_fa.school_job_id
        WHERE _fa.profile_id={profile} AND _fj.vacancy_id={vacancy}
          AND (_fa.feedback_recorded_at IS NOT NULL OR _fa.feedback IS NOT NULL)
    ) intent ORDER BY intent.stamp DESC,intent.priority,intent.feedback NULLS FIRST LIMIT 1"""


async def set_vacancy_feedback(session, profile_id, vacancy_id, feedback):
    await session.execute(sa.text(
        "INSERT INTO profile_vacancy_state (profile_id,vacancy_id,feedback,dismissed_at,updated_at,feedback_recorded_at) "
        "VALUES (:p,:v,:feedback,CASE WHEN :negative THEN clock_timestamp() END,clock_timestamp(),clock_timestamp()) "
        "ON CONFLICT (profile_id,vacancy_id) DO UPDATE SET feedback=excluded.feedback, "
        "dismissed_at=excluded.dismissed_at, "
        "feedback_recorded_at=excluded.feedback_recorded_at, "
        "updated_at=GREATEST(profile_vacancy_state.updated_at,clock_timestamp())"
    ), {"p": profile_id, "v": vacancy_id, "feedback": feedback,
        "negative": feedback in {"thumbs_down", "dismissed"}})
    # A canonical clear/like must not leave a contradictory school rejection.
    # No row is fabricated: historical observations retain their own identity.
    await session.execute(sa.text(
        "UPDATE school_applications a SET feedback=:feedback,version=a.version+1,"
        "feedback_recorded_at=clock_timestamp(),"
        "updated_at=GREATEST(a.updated_at,clock_timestamp()) FROM school_job_details j "
        "WHERE j.id=a.school_job_id AND j.vacancy_id=:v AND a.profile_id=:p "
        "AND (a.feedback IS DISTINCT FROM :feedback OR a.feedback_recorded_at IS NULL)"
    ), {"p": profile_id, "v": vacancy_id, "feedback": feedback})
