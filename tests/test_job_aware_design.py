"""Tool design follows the user's questions, not the profiled column order.

Regression for the AccuWeather test: the first measure in the table was
cloud_cover_perc_avg, so every tool used it, and question 1 was pinned to
the describe tool simply because it came first.
"""

from __future__ import annotations

from mcp_server_creator.designer import design_tools
from mcp_server_creator.models import (
    DataModality,
    DataSourceProfile,
    FieldProfile,
    FieldRole,
    QueryPattern,
    ServerSpec,
    SourceKind,
)
from mcp_server_creator.tools.generate_project import _match_path


def _weather_spec(jobs: list[str]) -> ServerSpec:
    def field(name, role, data_type="double", **extra):
        return FieldProfile(name=name, data_type=data_type, role=role, **extra)

    source = DataSourceProfile(
        name="forecast",
        kind=SourceKind.UC_TABLE,
        modality=DataModality.STRUCTURED,
        catalog="samples",
        schema_name="accuweather",
        table="forecast_daily_calendar_imperial",
        fields=[
            field("cloud_cover_perc_avg", FieldRole.MEASURE),
            field("country_code", FieldRole.DIMENSION, "string", cardinality_hint=40),
            field("city_name", FieldRole.DIMENSION, "string", cardinality_hint=45),
            field("date", FieldRole.DATE, "date"),
            field("temperature_max", FieldRole.MEASURE),
            field("uv_index_avg", FieldRole.MEASURE),
            field("cooling_degree_days", FieldRole.MEASURE),
        ],
    )
    return ServerSpec(
        server_name="weather-intel-mcp",
        purpose="Answer forecast questions for city planners.",
        jobs_to_be_done=jobs,
        data_sources=[source],
    )


def test_the_measure_the_user_asks_about_is_used():
    spec = _weather_spec(["Which city has the lowest UV index this weekend?"])
    tools, _ = design_tools(spec)
    rank = next(t for t in tools if t.pattern is QueryPattern.RANK)
    assert "uv_index_avg" in rank.name and "city_name" in rank.name


def test_each_question_seeds_the_tool_that_answers_it():
    job = "Which city has the lowest UV index this weekend?"
    spec = _weather_spec([job])
    tools, _ = design_tools(spec)
    seeded = [t for t in tools if job in t.eval_seeds]
    assert len(seeded) == 1
    assert seeded[0].pattern is QueryPattern.RANK
    describe = next(t for t in tools if t.pattern is QueryPattern.DESCRIBE_SOURCE)
    assert job not in describe.eval_seeds


def test_a_question_no_tool_covers_is_reported():
    spec = _weather_spec(["Zzz qqq?"])
    _, notes = design_tools(spec)
    assert any("No proposed tool clearly answers" in note for note in notes)


def test_without_questions_the_profiled_order_stands():
    tools, _ = design_tools(_weather_spec([]))
    rank = next(t for t in tools if t.pattern is QueryPattern.RANK)
    assert "cloud_cover_perc_avg" in rank.name


def test_include_contents_accepts_short_paths():
    paths = ["app.yaml", "src/pkg/tools/search.py", "src/pkg/tools/top.py"]
    assert _match_path("app.yaml", paths) == ("app.yaml", [])
    assert _match_path("tools/search.py", paths)[0] == "src/pkg/tools/search.py"
    assert _match_path("search.py", paths)[0] == "src/pkg/tools/search.py"
    assert _match_path("tools.py", paths)[0] is None
    assert _match_path("tools/", paths)[0] is None
