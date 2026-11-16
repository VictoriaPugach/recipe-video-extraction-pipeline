# Тесты метрики сравнения с эталоном (scripts/compare_gold.py).
# Метрика закреплена рубрикой (docs/evaluation.md): раскрытие в JSON Pointer,
# объединение путей, порог 80% без округления вверх. Ошибка в реализации
# метрики означала бы неверные оценочные числа, поэтому поведение проверяется.
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "compare_gold.py"

_spec = importlib.util.spec_from_file_location("compare_gold", SCRIPT)
compare_gold = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compare_gold)

flatten = compare_gold.flatten
compare = compare_gold.compare


def test_flatten_expands_lists_and_nested_fields():
    doc = {
        "title": "Омлет",
        "ingredients": [{"name": "Яйца", "quantity": {"value": 2, "unit": "pcs"}}],
        "note": None,
    }
    paths = flatten(doc)
    assert paths == {
        "/title": "Омлет",
        "/ingredients/0/name": "Яйца",
        "/ingredients/0/quantity/value": 2,
        "/ingredients/0/quantity/unit": "pcs",
        "/note": None,
    }


def test_flatten_empty_containers_are_terminal():
    # Пустой контейнер — конечное значение по рубрике, не раскрывается.
    assert flatten({"tags": [], "meta": {}}) == {"/tags": [], "/meta": {}}


def test_flatten_escapes_pointer_tokens():
    # RFC 6901: ~ -> ~0, / -> ~1 — иначе пути «a/b» и «a», «b» неразличимы.
    assert flatten({"a/b": 1, "x~y": 2}) == {"/a~1b": 1, "/x~0y": 2}


def test_compare_identical_documents():
    doc = {"title": "Омлет", "servings": 2}
    report = compare(doc, doc)
    assert report["matches"] == report["total"] == 2
    assert report["ratio"] == 1.0
    assert report["diffs"] == []


def test_missing_extra_and_wrong_lower_the_score():
    # Рубрика: пропущенное и лишнее поле увеличивают знаменатель и не
    # увеличивают числитель; неверное значение тоже не даёт совпадения.
    gold = {"title": "Омлет", "servings": 2, "extra": "x"}
    result = {"title": "Омлет", "servings": 3, "another": "y"}
    report = compare(gold, result)
    assert report["total"] == 4  # title, servings, extra, another
    assert report["matches"] == 1  # только title
    statuses = {d["pointer"]: d["status"] for d in report["diffs"]}
    assert statuses == {
        "/servings": "unequal",
        "/extra": "missing_in_result",
        "/another": "extra_in_result",
    }


def test_null_and_missing_are_distinguished_by_default():
    # Рубрика: отсутствующее поле и поле со значением null различаются.
    gold = {"title": "Омлет", "note": None}
    result = {"title": "Омлет"}
    report = compare(gold, result)
    assert report["matches"] == 1 and report["total"] == 2
    assert report["diffs"][0]["status"] == "missing_in_result"
    # Согласованная нормализация: null и отсутствие считаются совпадением.
    relaxed = compare(gold, result, absent_equals_null=True)
    assert relaxed["matches"] == 2 and relaxed["diffs"] == []


def test_exclude_removes_subtree():
    gold = {"title": "А", "source": {"type": "url", "url": "https://a"}}
    result = {"title": "А", "source": {"type": "upload"}}
    strict = compare(gold, result)
    # Объединение: /title, /source/type, /source/url; совпадает только /title.
    assert strict["matches"] == 1 and strict["total"] == 3
    without_source = compare(gold, result, excludes=("/source",))
    assert without_source["matches"] == without_source["total"] == 1


def _real_gold(name: str) -> Path:
    return ROOT / "data" / "expected_outputs" / f"{name}.json"


def test_cli_validate_only_accepts_contract_examples():
    result = compare_gold.main([
        "--validate-only",
        str(_real_gold("pasta")), str(_real_gold("omelette")), str(_real_gold("salad")),
    ])
    assert result == 0


def test_cli_validate_only_rejects_invalid_file(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "9.9.9"}), encoding="utf-8")
    assert compare_gold.main(["--validate-only", str(bad)]) == 1
    assert "FAIL" in capsys.readouterr().out


def test_cli_compare_self_scores_full_match():
    # Эталон против самого себя: 100%, код 0.
    assert compare_gold.main([str(_real_gold("pasta")), str(_real_gold("pasta"))]) == 0


def test_cli_compare_different_recipes_fails_threshold(capsys):
    # Разные рецепты дают низкий процент — код 1 и отчёт с расхождениями.
    assert compare_gold.main([str(_real_gold("pasta")), str(_real_gold("omelette"))]) == 1
    output = capsys.readouterr().out
    assert "FAIL" in output and "Расхождения:" in output


def test_cli_compare_requires_two_files():
    with pytest.raises(SystemExit) as excinfo:
        compare_gold.main([str(_real_gold("pasta"))])
    assert excinfo.value.code == 2


def test_cli_excludes_source_subtree():
    # /source ставит сервер из фактического входа; если нормализация согласована,
    # поддерево исключается и не влияет на процент.
    gold, result = _real_gold("pasta"), _real_gold("pasta")
    assert compare_gold.main(["--exclude", "/source", str(gold), str(result)]) == 0


# --- Ингредиенты сопоставляются по имени, а не по позиции списка ---
# Мотивация: сравнение по индексам означало, что один пропущенный ингредиент
# (пайплайн пропускал соль на плотных кадрах) или лишний (галлюцинация «Кокосовая
# стружка») сдвигал пары у всех последующих — одна реальная ошибка обрушивала
# весь процент за счёт чужих полей.

def _ing(name, value=None, unit=None, note=None):
    ing = {"name": name}
    if value is not None:
        ing["quantity"] = {"value": value, "unit": unit}
    if note is not None:
        ing["note"] = note
    return ing


def test_missing_ingredient_does_not_shift_the_rest():
    # Пропущенная «Соль» стоит ровно своих полей; «Перец» после неё
    # сравнивается со своим «Перцем», а не со сдвигом на одну позицию.
    gold = {"ingredients": [_ing("Яйца", 2, "pcs"), _ing("Соль", 1, "tsp"), _ing("Перец", 1, "pinch")]}
    result = {"ingredients": [_ing("Яйца", 2, "pcs"), _ing("Перец", 1, "pinch")]}
    report = compare(gold, result)
    statuses = {d["pointer"]: d["status"] for d in report["diffs"]}
    assert statuses == {
        "/ingredients/Соль/name": "missing_in_result",
        "/ingredients/Соль/quantity/value": "missing_in_result",
        "/ingredients/Соль/quantity/unit": "missing_in_result",
    }
    assert report["matches"] == 6 and report["total"] == 9


def test_extra_ingredient_in_the_middle_costs_only_itself():
    # Лишний ингредиент в середине списка не сдвигает пары остальных:
    # «Кокосовая стружка» — лишние три поля, Яйца/Мука/Соль совпали по именам.
    gold = {"ingredients": [_ing("Яйца", 2, "pcs"), _ing("Мука", 200, "g"), _ing("Соль", 1, "tsp")]}
    result = {"ingredients": [_ing("Яйца", 2, "pcs"), _ing("Кокосовая стружка", 30, "g"),
                              _ing("Мука", 200, "g"), _ing("Соль", 1, "tsp")]}
    report = compare(gold, result)
    statuses = {d["pointer"]: d["status"] for d in report["diffs"]}
    assert statuses == {
        "/ingredients/Кокосовая стружка/name": "extra_in_result",
        "/ingredients/Кокосовая стружка/quantity/value": "extra_in_result",
        "/ingredients/Кокосовая стружка/quantity/unit": "extra_in_result",
    }
    assert report["matches"] == 9 and report["total"] == 12


def test_names_match_regardless_of_case_and_spaces():
    # Согласованная нормализация «регистр и пробелы»: «Соль» и « соль » — пара.
    gold = {"ingredients": [_ing("Соль", 1, "tsp")]}
    result = {"ingredients": [_ing("соль ", 1, "tsp")]}
    report = compare(gold, result)
    assert report["diffs"] == [] and report["ratio"] == 1.0


def test_duplicate_ingredients_pair_in_order():
    gold = {"ingredients": [_ing("Соль", 1, "tsp"), _ing("Соль", 2, "tsp")]}
    result = {"ingredients": [_ing("соль", 1, "tsp"), _ing("СОЛЬ", 2, "tsp")]}
    report = compare(gold, result)
    assert report["diffs"] == [] and report["ratio"] == 1.0


def test_unique_substring_fallback_pairs_renamed_ingredient():
    # «Мука» против «Пшеничная мука»: уникальное вхождение подстрокой — пара.
    gold = {"ingredients": [_ing("Мука", 200, "g")]}
    result = {"ingredients": [_ing("Пшеничная мука", 200, "g")]}
    report = compare(gold, result)
    assert report["diffs"] == [] and report["ratio"] == 1.0


def test_ambiguous_substring_is_not_paired():
    # «масло» входит в оба варианта — кандидата два, пара не составляется:
    # сомнительное сопоставление остаётся в расхождениях, а не прячется.
    gold = {"ingredients": [_ing("масло", 50, "g")]}
    result = {"ingredients": [_ing("сливочное масло", 50, "g"), _ing("растительное масло", 50, "g")]}
    report = compare(gold, result)
    statuses = {d["pointer"]: d["status"] for d in report["diffs"]}
    assert statuses == {
        "/ingredients/масло/name": "missing_in_result",
        "/ingredients/масло/quantity/value": "missing_in_result",
        "/ingredients/масло/quantity/unit": "missing_in_result",
        "/ingredients/сливочное масло/name": "extra_in_result",
        "/ingredients/сливочное масло/quantity/value": "extra_in_result",
        "/ingredients/сливочное масло/quantity/unit": "extra_in_result",
        "/ingredients/растительное масло/name": "extra_in_result",
        "/ingredients/растительное масло/quantity/value": "extra_in_result",
        "/ingredients/растительное масло/quantity/unit": "extra_in_result",
    }


# --- Поля свободной формы исключаются флагами, а не оценкой строк ---

def test_skip_instructions_excludes_free_text_steps():
    # Один и тот же шаг в разных формулировках: без флага — расхождение,
    # с флагом текст шага из метрики выведен (оценивается вручную).
    gold = {"steps": [{"instruction": "Взбейте яйца.", "duration_seconds": None}]}
    result = {"steps": [{"instruction": "Яйца взбить", "duration_seconds": None}]}
    strict = compare(gold, result)
    assert strict["diffs"][0]["pointer"] == "/steps/0/instruction"
    relaxed = compare(gold, result, skip_instructions=True)
    assert relaxed["diffs"] == [] and relaxed["ratio"] == 1.0


def test_missing_steps_cannot_pass_through_null_durations():
    # Регрессия: рекомендованный пресет раньше давал 100% результату с одним
    # неверным шагом вместо трёх. Пропавшие инструкции исчезали из метрики,
    # а null-длительности несуществующих шагов засчитывались как совпадения.
    gold = {
        "title": "Тест",
        "steps": [
            {"instruction": "One", "duration_seconds": None},
            {"instruction": "Two", "duration_seconds": None},
            {"instruction": "Three", "duration_seconds": None},
        ],
    }
    result = {
        "title": "Тест",
        "steps": [{"instruction": "Completely wrong", "duration_seconds": None}],
    }
    report = compare(gold, result, absent_equals_null=True, skip_instructions=True)
    assert report["matches"] == 3  # title + существование и duration единственного шага
    assert report["total"] == 7
    assert report["ratio"] < 0.8
    assert {d["pointer"] for d in report["diffs"]} == {
        "/steps/1/instruction", "/steps/1/duration_seconds",
        "/steps/2/instruction", "/steps/2/duration_seconds",
    }


def test_skip_instructions_still_measures_duration():
    # Текст выведен, число — нет: неверная длительность остаётся расхождением.
    gold = {"steps": [{"instruction": "Жарьте 2 минуты", "duration_seconds": 120}]}
    result = {"steps": [{"instruction": "Готовьте две минуты", "duration_seconds": 180}]}
    report = compare(gold, result, skip_instructions=True)
    assert [d["pointer"] for d in report["diffs"]] == ["/steps/0/duration_seconds"]


def test_skip_notes_excludes_free_form_notes():
    # Счётное уточнение в note результата против пустого note эталона:
    # без --skip-notes это расхождение, с флагом — нет.
    gold = {"ingredients": [_ing("Чеснок", 3, "pcs")]}
    result = {"ingredients": [{"name": "Чеснок", "quantity": {"value": 3, "unit": "pcs"}, "note": "зубчика"}]}
    strict = compare(gold, result, absent_equals_null=True)
    assert [d["pointer"] for d in strict["diffs"]] == ["/ingredients/Чеснок/note"]
    relaxed = compare(gold, result, absent_equals_null=True, skip_notes=True)
    assert relaxed["diffs"] == [] and relaxed["ratio"] == 1.0


def test_cli_warns_on_indexed_ingredient_excludes(tmp_path, capsys):
    # Старый пресет с индексными исключениями больше не работает молча.
    gold, result = tmp_path / "g.json", tmp_path / "r.json"
    doc = {"ingredients": [_ing("Соль", 1, "tsp")]}
    gold.write_text(json.dumps(doc), encoding="utf-8")
    result.write_text(json.dumps(doc), encoding="utf-8")
    code = compare_gold.main(["--no-validate", "--exclude", "/ingredients/0/note", str(gold), str(result)])
    assert code == 0
    assert "ВНИМАНИЕ" in capsys.readouterr().err


def test_cli_skip_flags_thread_through(tmp_path):
    gold, result = tmp_path / "g.json", tmp_path / "r.json"
    gold.write_text(json.dumps({"title": "Т", "steps": [{"instruction": "А", "duration_seconds": None}]}), encoding="utf-8")
    result.write_text(json.dumps({"title": "Т", "steps": [{"instruction": "Б", "duration_seconds": None}]}), encoding="utf-8")
    assert compare_gold.main(["--no-validate", "--skip-instructions", str(gold), str(result)]) == 0
    assert compare_gold.main(["--no-validate", str(gold), str(result)]) == 1
