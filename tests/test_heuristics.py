from app.services.heuristics import heuristic_script, stable_appearance
from app.services.scenes import normalize_scenes, plan_scenes
from app.providers.llm import MockLLM, parse_json_loose
from app.timeline import line_spans, scene_durations, total_duration


def test_script_extracts_narration_and_speakers():
    text = ( __import__("pathlib").Path(__file__).resolve().parents[1] / "samples" / "rain-night.txt").read_text(encoding="utf-8")
    from app.services.importer import split_episodes

    episode = split_episodes(text).episodes[0]
    draft = heuristic_script(episode.text)
    speakers = {line["speaker"] for line in draft["lines"]}
    assert speakers == {"旁白", "林晚", "沈衡"}
    joined = " ".join(line["text"] for line in draft["lines"] if line["speaker"] == "林晚")
    assert "不要再往北走" in joined
    names = {item["name"] for item in draft["characters"]}
    assert "旁白" in names
    appearance = next(item["description"] for item in draft["characters"] if item["name"] == "林晚")
    assert appearance
    assert "林晚" in appearance


def test_pronoun_keeps_previous_speaker():
    draft = heuristic_script('林晚说道：“留下。”\n他道：“好。”\n')
    assert [line["speaker"] for line in draft["lines"]] == ["林晚", "林晚"]


def test_appearance_is_stable():
    assert stable_appearance("沈衡") == stable_appearance("沈衡")
    assert stable_appearance("沈衡") != stable_appearance("林晚")


def test_scenes_cover_every_line():
    draft = heuristic_script("雨还在下。\n林晚说道：“等一等。”\n沈衡说：“我在。”\n巷子空了。\n")
    llm = MockLLM()
    scenes = plan_scenes(
        draft["lines"],
        draft["characters"],
        llm,
    )
    normalize_scenes({"scenes": scenes}, len(draft["lines"]))
    assert scenes[0]["start_line"] == 0
    assert scenes[-1]["end_line"] == len(draft["lines"]) - 1
    assert "角色外观必须保持一致" in scenes[0]["prompt"]
    assert "林晚" in scenes[0]["prompt"]


def test_loose_json_fence():
    data = parse_json_loose('```json\n{"lines":[{"speaker":"旁白","text":"夜"}]}\n```')
    assert data["lines"][0]["text"] == "夜"


def test_timeline_matches_audio_length():
    durations = [1.0, 1.0, 1.0]
    spans = line_spans(durations, gap=0.1)
    assert spans[1].start == 1.1
    assert total_duration(durations, gap=0.1) == 3.2
    pieces = scene_durations([(0, 1), (2, 2)], durations, gap=0.1)
    assert round(sum(pieces), 5) == 3.2
