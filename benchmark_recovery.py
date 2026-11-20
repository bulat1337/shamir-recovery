"""Сравнение восстановления по первым трём файлам и перебором троек."""

import argparse
import csv
import json
import platform
from itertools import permutations
from pathlib import Path
from secrets import token_bytes
from tempfile import TemporaryDirectory
from time import perf_counter_ns

import Crypto

from benchmark_ieee import cpu_name
from shamir_project.core import FormatError, Recovery, load_inputs, recover, write_file
from shamir_project.demo import scenarios

SIZES = (4096, 1024**2)
EXPECTED = {"intact": 120, "one_error": 48, "two_errors": 12}


def measure(container, order, recovered, search):
    start = perf_counter_ns()
    try:
        inputs = load_inputs(container, list(order))
    except FormatError:
        return Recovery("bad_container"), None, perf_counter_ns() - start
    core_start = perf_counter_ns()
    report = recover(inputs, search=search)
    core_time = perf_counter_ns() - core_start
    if report.valid:
        write_file(recovered, report.plaintext)
    return report, core_time, perf_counter_ns() - start


def run(output, repetitions):
    output.mkdir(parents=True, exist_ok=True)
    environment = {
        "cpu": cpu_name(), "os": platform.platform(), "python": platform.python_version(),
        "pycryptodome": Crypto.__version__, "sizes_bytes": list(SIZES), "repetitions": repetitions,
    }
    (output / "recovery_environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    with TemporaryDirectory(prefix="shamir-bench-") as directory, (
        output / "recovery_runs.csv"
    ).open("w", newline="", encoding="utf-8") as log:
        writer = csv.writer(log)
        writer.writerow(("size_bytes", "scenario", "permutation", "order", "repeat", "method",
                         "status", "attempts", "matched_bytes", "search_ns", "total_ns"))
        scratch = Path(directory)
        destination = scratch / "recovered"
        for size in SIZES:
            data = token_bytes(size)
            for case in scenarios(data):
                location = scratch / f"{size}-{case.name}"
                location.mkdir()
                container = location / "input.sssf"
                container.write_bytes(case.container)
                paths = []
                for name, raw in case.files:
                    path = location / name
                    path.write_bytes(raw)
                    paths.append(path)
                orders = list(dict.fromkeys(permutations(paths)))
                baseline_wins = 0
                for number, order in enumerate(orders, start=1):
                    for repetition in range(1, repetitions + 1):
                        for search in ((False, True) if (number + repetition) % 2 else (True, False)):
                            report, core_ns, total_ns = measure(container, order, destination, search)
                            matched = destination.is_file() and destination.read_bytes() == data
                            if report.valid != matched or (not matched and destination.exists()):
                                raise RuntimeError(f"{case.name}: выдан неожиданный файл")
                            destination.unlink(missing_ok=True)
                            if search and matched != case.can_recover:
                                raise RuntimeError(f"{case.name}: неожиданный результат перебора")
                            if not search and matched and repetition == 1:
                                baseline_wins += 1
                            writer.writerow((size, case.name, number, " | ".join(p.name for p in order),
                                             repetition, "search" if search else "first", report.status,
                                             len(report.attempts), int(matched), core_ns, total_ns))
                if case.name in EXPECTED and baseline_wins != EXPECTED[case.name]:
                    raise RuntimeError(f"{case.name}: базовый метод дал {baseline_wins} успехов")
            log.flush()
            print(f"{size} байт: проверены все сценарии", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("число повторов должно быть положительным")
    run(args.out, args.repeats)
