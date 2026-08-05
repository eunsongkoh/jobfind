from jobfind.config import Profile
from jobfind.models import Job
from jobfind.scoring.scorer import _build_user_prompt, score_job


class FakeProvider:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc

    def complete(self, system_prompt, user_prompt, *, temperature, max_tokens, response_format=None):
        if self.exc:
            raise self.exc
        return self.response


def _job() -> Job:
    return Job(
        id="1",
        source="test",
        track="new_grad",
        title="Software Engineer New Grad",
        company="Acme",
        location="Remote",
        job_url="https://example.com/1",
        date_posted=None,
        date_detected="2026-08-04T00:00:00+00:00",
    )


def _profile() -> Profile:
    return Profile(role_target=["Software Engineer"], skills=["Python"])


def test_parses_clean_json_response():
    provider = FakeProvider(response='{"score": 85, "reason": "Great fit"}')

    scored = score_job(_job(), _profile(), provider)

    assert scored.score == 85
    assert scored.rationale == "Great fit"


def test_parses_json_wrapped_in_extra_text():
    provider = FakeProvider(response='Here is my answer:\n```json\n{"score": 42, "reason": "ok"}\n```')

    scored = score_job(_job(), _profile(), provider)

    assert scored.score == 42
    assert scored.rationale == "ok"


def test_falls_back_to_regex_when_json_malformed():
    provider = FakeProvider(response="I'd give this a score: 77 out of 100 because it's a solid match")

    scored = score_job(_job(), _profile(), provider)

    assert scored.score == 77
    assert scored.rationale == "parsed_from_fallback"


def test_unparseable_response_scores_zero():
    provider = FakeProvider(response="I cannot help with that.")

    scored = score_job(_job(), _profile(), provider)

    assert scored.score == 0
    assert scored.rationale == "parse_error"


def test_provider_error_scores_zero_without_raising():
    provider = FakeProvider(exc=RuntimeError("network down"))

    scored = score_job(_job(), _profile(), provider)

    assert scored.score == 0
    assert scored.rationale == "provider_error"


def test_score_is_clamped_to_0_100():
    provider = FakeProvider(response='{"score": 150, "reason": "over"}')

    scored = score_job(_job(), _profile(), provider)

    assert scored.score == 100


def test_parses_full_structured_response_with_confidence():
    provider = FakeProvider(response='{"score": 72, "confidence": 60, "reason": "Decent overlap in skills"}')

    scored = score_job(_job(), _profile(), provider)

    assert scored.score == 72
    assert scored.confidence == 60
    assert scored.rationale == "Decent overlap in skills"


def test_confidence_defaults_to_zero_when_absent_from_loose_json():
    provider = FakeProvider(response='{"score": 65, "reason": "no confidence key here"}')

    scored = score_job(_job(), _profile(), provider)

    assert scored.confidence == 0


def test_prompt_includes_recommendation_mode():
    profile = Profile(role_target=["Software Engineer"], skills=["Python"], recommendation_mode="broad")

    prompt = _build_user_prompt(_job(), profile)

    assert "Recommendation Mode:\nbroad" in prompt


def test_prompt_defaults_recommendation_mode_to_personalized():
    prompt = _build_user_prompt(_job(), _profile())

    assert "Recommendation Mode:\npersonalized" in prompt
