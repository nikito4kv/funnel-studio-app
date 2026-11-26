"""Каждое поле FunnelStep движка знает своё место в модели Студии."""
from studio.convert.mapping import covered_step_fields
from studio.engine_ref.loader import FunnelConfig, FunnelStep


def test_every_engine_step_field_is_mapped():
    missing = set(FunnelStep.model_fields) - covered_step_fields()
    assert not missing, f"новые поля движка без места в модели: {sorted(missing)}"


def test_no_stale_mapping():
    stale = covered_step_fields() - set(FunnelStep.model_fields)
    assert not stale, f"в карте поля, которых нет в движке: {sorted(stale)}"


def test_funnel_level_fields_known():
    assert set(FunnelConfig.model_fields) == {
        "id", "steps", "branch_router", "webinar_room", "entry_variants",
        "air_email_profile", "max_video_note_caption", "resend_entry_on_repeat"}


def test_model_step_fields_mapped_or_studio_only():
    """Каждое поле групп шага модели либо выгружается в yaml, либо помечено «только Студия»."""
    from studio.convert.mapping import STEP_FIELD_PATHS, STUDIO_ONLY_PATHS
    from studio.model.funnel import Conditions
    mapped = {p[1] for p in STEP_FIELD_PATHS.values() if p[0] == "conditions"}
    only = {p[1] for p in STUDIO_ONLY_PATHS if p[0] == "conditions"}
    assert not mapped & only
    assert set(Conditions.model_fields) == mapped | only


def test_link_click_is_studio_only_and_not_exported():
    """«Кликнул ссылку» – гейт движка Студии: в yaml bot-engine не уходит, экспорт предупреждает."""
    import uuid

    from studio.convert import export_data, import_data
    from studio.convert.to_engine import studio_only_notes
    from studio.model.funnel import Funnel, Step
    s = Step(id=uuid.uuid4(), key="thanks", conditions={"link_click": "funnel_offer"})
    f = Funnel(id="f", steps=[s], edges=[])
    data = export_data(f)
    assert all("link_click" not in k for k in data["steps"][0])
    notes = studio_only_notes(f)
    assert len(notes) == 1 and "«thanks»" in notes[0] and "кликнул ссылку" in notes[0]
    back = import_data(data, "x")
    assert back.steps[0].conditions.link_click == "" and not back.steps[0].advanced
