#!/usr/bin/env python3
# Оценка извлечённого рецепта против ручного эталона по метрике рубрики
# (docs/evaluation.md): оба JSON раскрываются в пары «JSON Pointer → значение»,
# берётся объединение путей, совпадение = пути, присутствующие в обоих
# документах с равными значениями / размер объединения × 100%.
#
# Правила раскрытия из рубрики:
#   - скаляр, null и пустой контейнер — конечные значения;
#   - каждый элемент непустого списка и каждое вложенное поле раскрываются,
#     индексы списков входят в путь;
#   - отсутствующее поле и поле со значением null различаются
#     (если иная нормализация не согласована — см. --absent-equals-null).
#
# Отличие от наивного раскрытия: ингредиенты сопоставляются ПО ИМЕНИ, а не по
# позиции списка (регистр и пробелы не важны; дубликаты — по порядку; остатки —
# по уникальному вхождению подстрокой: «мука» ⊂ «пшеничная мука»; при двух и
# более кандидатах пара не составляется). Пропущенный или лишний ингредиент
# стоит ровно своих полей и не сдвигает пары остальных. Поля свободной формы
# (--skip-notes, --skip-instructions) исключаются из метрики — их качество
# оценивается вручную (docs/real-testing.md, п. 4).
#
# Примеры:
#   # Проверить файлы эталонов на соответствие контракту Recipe 1.0.0:
#   python scripts/compare_gold.py --validate-only data/expected_outputs/*.json
#
#   # Оценить один прогон против эталона:
#   python scripts/compare_gold.py data/expected_outputs/pasta.json run_pasta.json
#
#   # Пресет реальной проверки (docs/real-testing.md, п. 4):
#   python scripts/compare_gold.py --exclude /source --absent-equals-null \
#       --skip-notes --skip-instructions gold.json result.json
#
# Коды выхода: 0 — порог достигнут (или все файлы валидны);
#              1 — порог не достигнут / есть ошибки валидации;
#              2 — ошибка использования или чтения файлов.
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

DEFAULT_THRESHOLD = 0.8  # рубрика: не менее 80% для каждого видео

_INSTRUCTION_POINTER = re.compile(r"^/steps/\d+/instruction$")
_INDEXED_INGREDIENT_EXCLUDE = re.compile(r"^/ingredients/\d+($|/)")


def escape_token(token: str) -> str:
    # Экранирование RFC 6901: сначала ~ -> ~0, затем / -> ~1.
    return str(token).replace("~", "~0").replace("/", "~1")


def flatten(value, pointer: str = "", out: dict | None = None) -> dict:
    """Раскрывает JSON до пар «указатель → конечное значение»."""
    if out is None:
        out = {}
    if isinstance(value, dict) and value:
        for key, child in value.items():
            flatten(child, f"{pointer}/{escape_token(key)}", out)
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            flatten(child, f"{pointer}/{index}", out)
    else:
        out[pointer] = value  # скаляр, null, [] и {} — конечные значения
    return out


def _excluded(pointer: str, excludes: tuple[str, ...]) -> bool:
    # Точное совпадение или вложенность под исключённым поддеревом.
    return any(pointer == root or pointer.startswith(f"{root}/") for root in excludes)


def _equal(left, right) -> bool:
    # True/1 и False/0 в Python равны — для JSON-значений различаем булевы и числа.
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    return left == right


def _array_element_missing(pointer: str, other_paths: dict) -> bool:
    """True, если отсутствует весь элемент списка, а не одно nullable-поле.

    Например, для /steps/2/duration_seconds проверяем, есть ли в другом
    документе хоть один путь внутри /steps/2/. Иначе absent-equals-null мог
    бы засчитать длительность полностью отсутствующего шага как совпадение.
    """
    parent = pointer.rsplit("/", 1)[0]
    if not re.search(r"/\d+$", parent):
        return False
    prefix = f"{parent}/"
    return not any(other.startswith(prefix) for other in other_paths)


def _compare_path_maps(gold_paths: dict, result_paths: dict, absent_equals_null: bool) -> tuple[int, list[dict]]:
    """Общая сверка двух карт «указатель → значение»: совпадения и расхождения."""
    matches = 0
    diffs: list[dict] = []
    for pointer in sorted(set(gold_paths) | set(result_paths)):
        in_gold, in_result = pointer in gold_paths, pointer in result_paths
        if in_gold and in_result:
            if _equal(gold_paths[pointer], result_paths[pointer]):
                matches += 1
            else:
                diffs.append({"pointer": pointer, "status": "unequal", "gold": gold_paths[pointer], "result": result_paths[pointer]})
        elif in_gold:
            # null равен отсутствующему полю только внутри существующего
            # элемента списка. Полностью пропущенный шаг совпадением не бывает.
            if (absent_equals_null and gold_paths[pointer] is None
                    and not _array_element_missing(pointer, result_paths)):
                matches += 1
            else:
                diffs.append({"pointer": pointer, "status": "missing_in_result", "gold": gold_paths[pointer]})
        else:
            if (absent_equals_null and result_paths[pointer] is None
                    and not _array_element_missing(pointer, gold_paths)):
                matches += 1
            else:
                diffs.append({"pointer": pointer, "status": "extra_in_result", "result": result_paths[pointer]})
    return matches, diffs


def _ingredient_list(doc) -> list[dict]:
    ings = doc.get("ingredients") if isinstance(doc, dict) else None
    if not ings:
        return []
    if not isinstance(ings, list) or any(not isinstance(item, dict) for item in ings):
        raise ValueError("ingredients должны быть списком объектов — проверьте документы контрактом")
    return ings


def _norm_name(ingredient: dict) -> str:
    name = ingredient.get("name")
    if not isinstance(name, str):
        raise ValueError("у каждого ингредиента должно быть строковое поле name — проверьте документы контрактом")
    return name.strip().casefold()


def _ingredient_paths(ingredient: dict, skip_notes: bool) -> dict:
    """Раскрывает один ингредиент в относительные указатели («/name», «/quantity/value», …)."""
    paths = flatten(ingredient)
    if skip_notes:
        paths = {p: v for p, v in paths.items() if p != "/note"}
    return paths


def _pair_ingredients(gold_ings: list[dict], result_ings: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Сопоставляет ингредиенты по имени, а не по позиции списка.

    1) точное совпадение нормализованных имён (регистр и пробелы не важны),
       дубликаты — по порядку;
    2) остатки — уникальное вхождение одного имени в другое как подстроки;
       при двух и более кандидатах пара не составляется: сомнительное
       сопоставление остаётся в расхождениях, а не прячется.
    Возвращает (пары (i, j), неверифицированные индексы эталона, результата).
    """
    gold_keys = [_norm_name(ing) for ing in gold_ings]
    result_keys = [_norm_name(ing) for ing in result_ings]
    result_by_name: dict[str, list[int]] = {}
    for j, key in enumerate(result_keys):
        result_by_name.setdefault(key, []).append(j)
    pairs: list[tuple[int, int]] = []
    used_result: set[int] = set()
    for i, key in enumerate(gold_keys):
        queue = result_by_name.get(key)
        if queue:
            j = queue.pop(0)
            used_result.add(j)
            pairs.append((i, j))
    paired_gold = {i for i, _ in pairs}
    gold_left = [i for i in range(len(gold_ings)) if i not in paired_gold]
    result_left = [j for j in range(len(result_ings)) if j not in used_result]
    for i in list(gold_left):
        candidates = [
            j for j in result_left
            if gold_keys[i] and result_keys[j]
            and (gold_keys[i] in result_keys[j] or result_keys[j] in gold_keys[i])
        ]
        if len(candidates) == 1:
            j = candidates[0]
            pairs.append((i, j))
            gold_left.remove(i)
            result_left.remove(j)
    return pairs, gold_left, result_left


def compare(gold: dict, result: dict, excludes: tuple[str, ...] = (), absent_equals_null: bool = False,
            skip_notes: bool = False, skip_instructions: bool = False) -> dict:
    gold_flat = flatten(gold)
    result_flat = flatten(result)
    if skip_instructions:
        # Исключаем СОДЕРЖИМОЕ инструкций, но сохраняем их пути-маркеры:
        # одинаковый маркер засчитывается только для шага, существующего в обоих
        # документах. Полностью пропущенные/лишние шаги остаются расхождениями.
        marker = "<excluded-by-skip-instructions>"
        gold_flat = {p: marker if _INSTRUCTION_POINTER.match(p) else v
                     for p, v in gold_flat.items()}
        result_flat = {p: marker if _INSTRUCTION_POINTER.match(p) else v
                       for p, v in result_flat.items()}

    matches = 0
    diffs: list[dict] = []

    # Ингредиенты: пары по имени. Указатели расхождений строятся от имени
    # (/ingredients/Соль/quantity/value), поэтому --exclude работает и здесь.
    gold_ings = _ingredient_list(gold)
    result_ings = _ingredient_list(result)
    gold_ings_paths = [_ingredient_paths(ing, skip_notes) for ing in gold_ings]
    result_ings_paths = [_ingredient_paths(ing, skip_notes) for ing in result_ings]
    pairs, gold_left, result_left = _pair_ingredients(gold_ings, result_ings)
    for i, j in sorted(pairs):
        base = f"/ingredients/{escape_token(gold_ings[i]['name'])}"
        matches += 1  # /name: совпадение по построению — пара найдена по имени
        gold_pair = {f"{base}{p}": v for p, v in gold_ings_paths[i].items()
                     if p != "/name" and not _excluded(f"{base}{p}", excludes)}
        result_pair = {f"{base}{p}": v for p, v in result_ings_paths[j].items()
                       if p != "/name" and not _excluded(f"{base}{p}", excludes)}
        part, part_diffs = _compare_path_maps(gold_pair, result_pair, absent_equals_null)
        matches += part
        diffs.extend(part_diffs)
    for i in gold_left:
        base = f"/ingredients/{escape_token(gold_ings[i]['name'])}"
        for p, v in sorted(gold_ings_paths[i].items()):
            pointer = f"{base}{p}"
            if _excluded(pointer, excludes):
                continue
            diffs.append({"pointer": pointer, "status": "missing_in_result", "gold": v})
    for j in result_left:
        base = f"/ingredients/{escape_token(result_ings[j]['name'])}"
        for p, v in sorted(result_ings_paths[j].items()):
            pointer = f"{base}{p}"
            if _excluded(pointer, excludes):
                continue
            diffs.append({"pointer": pointer, "status": "extra_in_result", "result": v})

    # Всё остальное — прежняя сверка по указателям (включая «/ingredients»
    # пустого списка: у несоставленного списка нет элементов для пар).
    rest_gold = {p: v for p, v in gold_flat.items()
                 if not p.startswith("/ingredients/") and not _excluded(p, excludes)}
    rest_result = {p: v for p, v in result_flat.items()
                   if not p.startswith("/ingredients/") and not _excluded(p, excludes)}
    rest_matches, rest_diffs = _compare_path_maps(rest_gold, rest_result, absent_equals_null)
    matches += rest_matches
    diffs.extend(rest_diffs)

    diffs.sort(key=lambda d: d["pointer"])
    total = matches + len(diffs)
    if not total:
        raise ValueError("Объединение путей пусто — сравнивать нечего")
    return {
        "matches": matches,
        "total": total,
        "ratio": matches / total,
        "diffs": diffs,
    }


def _short(value, limit: int = 60) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def load_json(path: Path):
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        raise SystemExit(f"Не удалось прочитать {path}: {exc}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path} — не корректный JSON: {exc}")


def validate_json_text(text: str) -> str | None:
    """Возвращает сообщение об ошибке контракта или None, если документ валиден."""
    try:
        from fropti_core_contracts import validate_recipe_json
    except ImportError:
        return "пакет fropti_core_contracts не установлен (make install)"
    try:
        validate_recipe_json(text)
    except Exception as exc:  # noqa: BLE001 - любая ошибка валидации = невалидный документ
        return _short(str(exc), 200)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Оценка извлечённого рецепта против ручного эталона (метрика рубрики) "
        "и проверка эталонов контракту Recipe 1.0.0.",
    )
    parser.add_argument("paths", nargs="+", help="эталон и результат (оценка) или файлы эталонов (--validate-only)")
    parser.add_argument("--validate-only", action="store_true", help="только проверить файлы контракту, без оценки")
    parser.add_argument("--exclude", action="append", default=[], metavar="POINTER",
                        help="исключить поддерево из оценки, например /source (можно повторять); "
                        "для ингредиентов путь строится от имени: /ingredients/соль/note")
    parser.add_argument("--absent-equals-null", action="store_true",
                        help="считать null и отсутствующее поле совпадением (согласованная нормализация)")
    parser.add_argument("--skip-notes", action="store_true",
                        help="исключить note ингредиентов из метрики (свободная форма, оценивается вручную)")
    parser.add_argument("--skip-instructions", action="store_true",
                        help="исключить тексты шагов из метрики (свободная форма, оценивается вручную)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                        help=f"порог совпадения, по умолчанию {DEFAULT_THRESHOLD}")
    parser.add_argument("--json", action="store_true", help="машинночитаемый вывод (один JSON-объект)")
    parser.add_argument("--no-validate", action="store_true", help="не проверять документы контракту перед оценкой")
    args = parser.parse_args(argv)

    for root in args.exclude:
        if _INDEXED_INGREDIENT_EXCLUDE.match(root):
            print("ВНИМАНИЕ: индексные исключения /ingredients/<i>/... больше не действуют — "
                  "ингредиенты сопоставляются по имени, а не по позиции. Для note используйте "
                  "--skip-notes, для конкретного ингредиента — путь от имени (/ingredients/соль/note).",
                  file=sys.stderr)

    if args.validate_only:
        failures = 0
        for raw in args.paths:
            path = Path(raw)
            error = validate_json_text(path.read_text(encoding="utf-8")) if path.exists() else "файл не найден"
            if error:
                failures += 1
                print(f"FAIL {path}: {error}")
            else:
                print(f"OK   {path}: соответствует Recipe 1.0.0")
        return 1 if failures else 0

    if len(args.paths) != 2:
        parser.error("для оценки нужны ровно два файла: эталон и результат (или --validate-only со списком файлов)")
    if not 0 < args.threshold <= 1:
        parser.error("--threshold должен быть в интервале (0; 1]")

    gold_path, result_path = (Path(p) for p in args.paths)
    gold_doc, result_doc = load_json(gold_path), load_json(result_path)

    if not args.no_validate:
        errors = []
        for name, doc in (("эталон", gold_doc), ("результат", result_doc)):
            error = validate_json_text(json.dumps(doc, ensure_ascii=False))
            if error:
                errors.append(f"{name}: {error}")
        if errors:
            # Невалидный эталон или результат — оценка бессмысленна, рубрика
            # требует сначала привести документы к контракту.
            for error in errors:
                print(f"FAIL {error}", file=sys.stderr)
            print("Подсказка: приведите документы к Recipe 1.0.0 или запустите с --no-validate.", file=sys.stderr)
            return 1

    try:
        report = compare(gold_doc, result_doc, tuple(args.exclude), args.absent_equals_null,
                         args.skip_notes, args.skip_instructions)
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 2

    passed = report["ratio"] >= args.threshold
    # Процент для показа округляется вниз до 0.1%: рубрика запрещает округление
    # вверх, а решение принимается по точной дроби, не по отображаемому числу.
    shown = math.floor(report["ratio"] * 1000) / 10
    if args.json:
        print(json.dumps({
            "gold": str(gold_path), "result": str(result_path),
            "matches": report["matches"], "total": report["total"],
            "percent": shown, "ratio": report["ratio"],
            "threshold": args.threshold, "passed": passed,
            "diffs": report["diffs"],
        }, ensure_ascii=False, indent=2))
    else:
        print(f"Эталон:   {gold_path}")
        print(f"Результат: {result_path}")
        print(f"Пути в объединении: {report['total']}; совпадений: {report['matches']}; расхождений: {len(report['diffs'])}.")
        print(f"Совпадение: {report['matches']}/{report['total']} = {shown}% (порог {args.threshold * 100:g}%) — {'PASS' if passed else 'FAIL'}")
        if report["diffs"]:
            print("Расхождения:")
            for diff in report["diffs"]:
                pointer = diff["pointer"]
                if diff["status"] == "missing_in_result":
                    print(f"  {pointer}  [нет в результате]  эталон={_short(diff['gold'])}")
                elif diff["status"] == "extra_in_result":
                    print(f"  {pointer}  [лишнее в результате]  результат={_short(diff['result'])}")
                else:
                    print(f"  {pointer}  эталон={_short(diff['gold'])}  результат={_short(diff['result'])}")
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        # Вывод обрезан получателем (head, less) — это не ошибка оценки.
        # Перенаправляем stdout в devnull, чтобы Python не упал вторично при
        # финальной промывке буферов.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(1)
