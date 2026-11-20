"""Независимое повторение измерений из раздела V статьи Ferreira и Borges."""

import argparse
import csv
import json
import platform
import random
import subprocess
import tracemalloc
from functools import partial
from pathlib import Path
from time import perf_counter_ns

P = 2013265921
GENERATOR = 31
SECRET = 987654321
SIZES = (128, 256, 512, 1024, 2048, 4096, 8192)


def inverse(number):
    return pow(number % P, P - 2, P)


def root(size, backwards):
    power = (P - 1) // size
    return pow(GENERATOR, -power if backwards else power, P)


def twiddles(size):
    tables = []
    width = 2
    while width <= size:
        step = root(width, True)
        weights = [1]
        for _ in range(width // 2 - 1):
            weights.append(weights[-1] * step % P)
        tables.append(weights)
        width *= 2
    return tables, inverse(size)


def transform(values, backwards=False, prepared=None):
    a = list(values)
    size = len(a)
    j = 0
    for i in range(1, size):
        bit = size >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            a[i], a[j] = a[j], a[i]

    width, stage = 2, 0
    while width <= size:
        half = width // 2
        if prepared is None:
            step = root(width, backwards)
            for start in range(0, size, width):
                weight = 1
                for i in range(start, start + half):
                    u, v = a[i], a[i + half] * weight % P
                    a[i], a[i + half] = (u + v) % P, (u - v) % P
                    weight = weight * step % P
        else:
            weights = prepared[0][stage]
            for start in range(0, size, width):
                for offset, weight in enumerate(weights):
                    i = start + offset
                    u, v = a[i], a[i + half] * weight % P
                    a[i], a[i + half] = (u + v) % P, (u - v) % P
        width *= 2
        stage += 1
    if backwards:
        factor = inverse(size) if prepared is None else prepared[1]
        return [value * factor % P for value in a]
    return a


def example(size):
    rng = random.Random(50101 + size)  # Только данные эксперимента из статьи.
    coeff = [SECRET] + [rng.randrange(P) for _ in range(size - 2)] + [rng.randrange(1, P)]
    samples = transform(coeff + [0] * size)
    step = root(size, False)
    nodes = [1]
    for _ in range(size - 1):
        nodes.append(nodes[-1] * step % P)
    return tuple(nodes), tuple(samples[::2]), coeff


def lagrange_weights(xs):
    weights = []
    for j, xj in enumerate(xs):
        numerator, denominator = 1, 1
        for i, xi in enumerate(xs):
            if i != j:
                numerator = numerator * (-xi) % P
                denominator = denominator * (xj - xi) % P
        weights.append(numerator * inverse(denominator) % P)
    return weights


def weighted_sum(weights, ys):
    return sum(w * y for w, y in zip(weights, ys)) % P


def barycentric_weights(xs):
    weights = []
    for j, xj in enumerate(xs):
        denominator = 1
        for i, xi in enumerate(xs):
            if i != j:
                denominator = denominator * (xj - xi) % P
        weights.append(inverse(denominator))
    return weights


def barycentric(xs, weights, ys):
    at_zero = [weight * inverse(-x) % P for x, weight in zip(xs, weights)]
    return weighted_sum(at_zero, ys) * inverse(sum(at_zero)) % P


def newton(xs, ys):
    values = list(ys)
    for order in range(1, len(xs)):
        for i in range(len(xs) - 1, order - 1, -1):
            values[i] = (values[i] - values[i - 1]) * inverse(xs[i] - xs[i - order]) % P
    result = values[-1]
    for i in range(len(xs) - 2, -1, -1):
        result = (values[i] - xs[i] * result) % P
    return result


def neville(xs, ys):
    values = list(ys)
    for width in range(1, len(xs)):
        for i in range(len(xs) - width):
            j = i + width
            values[i] = (xs[j] * values[i] - xs[i] * values[i + 1]) * inverse(xs[j] - xs[i]) % P
    return values[0]


def aitken(xs, ys):
    # На шаге j каждая строка интерполирует точки 0..j-1 и свою точку i.
    values = list(ys)
    for j, xj in enumerate(xs):
        for i in range(j + 1, len(xs)):
            xi = xs[i]
            values[i] = (xi * values[j] - xj * values[i]) * inverse(xi - xj) % P
    return values[-1]


def vandermonde(xs, ys):
    matrix = []
    for x, y in zip(xs, ys):
        row = [1]
        for _ in range(len(xs) - 1):
            row.append(row[-1] * x % P)
        matrix.append(row + [y])

    size = len(xs)
    for j in range(size):
        pivot = matrix[j]
        scale = inverse(pivot[j])  # Для разных узлов ведущие миноры Вандермонда ненулевые.
        for i in range(j + 1, size):
            row = matrix[i]
            factor = row[j] * scale % P
            for h in range(j + 1, size + 1):
                row[h] = (row[h] - factor * pivot[h]) % P
    result = list(ys)
    for i in range(size - 1, -1, -1):
        row = matrix[i]
        tail = sum(row[j] * result[j] for j in range(i + 1, size))
        result[i] = (row[-1] - tail) * inverse(row[i]) % P
    return result[0]


def timed(operation, *args):
    start = perf_counter_ns()
    result = operation(*args)
    return result, (perf_counter_ns() - start) / 1e9


def peak_bytes(operation):
    tracemalloc.start()
    try:
        operation()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def cpu_name():
    if platform.system() == "Darwin":
        return subprocess.check_output(["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
    return platform.processor() or platform.machine()


def run(output, sizes, repetitions, extra_max, memory_max):
    output.mkdir(parents=True, exist_ok=True)
    environment = {
        "cpu": cpu_name(), "os": platform.platform(), "python": platform.python_version(),
        "p": P, "secret": SECRET, "generator": GENERATOR, "sizes": list(sizes),
        "repetitions": repetitions, "extra_max": extra_max, "memory_max": memory_max,
    }
    (output / "ieee_environment.json").write_text(json.dumps(environment, indent=2) + "\n")
    with (output / "ieee_timings.csv").open("w", newline="") as log, (
        output / "ieee_memory.csv"
    ).open("w", newline="") as mem:
        writer, memory = csv.writer(log), csv.writer(mem)
        writer.writerow(("threshold", "shares", "repeat", "method", "phase", "seconds"))
        memory.writerow(("threshold", "method", "phase", "peak_python_bytes"))

        for k in sizes:
            xs, ys, coeff = example(k)
            for x, expected in ((xs[0], ys[0]), (xs[k // 3], ys[k // 3]), (xs[-1], ys[-1])):
                evaluation = 0
                for value in reversed(coeff):
                    evaluation = (evaluation * x + value) % P
                if evaluation != expected:
                    raise RuntimeError("Проверка сгенерированных долей не прошла")
            if transform(ys, True) != coeff or transform(ys, True, twiddles(k)) != coeff:
                raise RuntimeError("Проверка обратной NTT не прошла")
            methods = {
                "Lagrange": (partial(lagrange_weights, xs), lambda w: weighted_sum(w, ys)),
                "Barycentric": (partial(barycentric_weights, xs), lambda w: barycentric(xs, w, ys)),
                "NTT_cached": (partial(twiddles, k), lambda w: transform(ys, True, w)[0]),
            }
            direct = {"NTT": lambda: transform(ys, True)[0]}
            if k <= extra_max:
                direct.update({fn.__name__: partial(fn, xs, ys) for fn in (newton, neville, aitken, vandermonde)})

            for repetition in range(1, repetitions + 1):
                for method, (make, use) in methods.items():
                    preparation, prep_sec = timed(make)
                    result, hot_sec = timed(use, preparation)
                    if result != SECRET:
                        raise RuntimeError(f"{method}, k={k}: секрет не восстановлен")
                    for phase, seconds in (("prepare", prep_sec), ("hot", hot_sec), ("cold", prep_sec + hot_sec)):
                        writer.writerow((k, 2 * k, repetition, method, phase, seconds))
                for method, operation in direct.items():
                    result, seconds = timed(operation)
                    if result != SECRET:
                        raise RuntimeError(f"{method}, k={k}: секрет не восстановлен")
                    writer.writerow((k, 2 * k, repetition, method, "cold", seconds))
            log.flush()
            for method, (make, use) in methods.items():
                if k <= memory_max or method == "NTT_cached":
                    memory.writerow((k, method, "cold", peak_bytes(lambda: use(make()))))
                    tables = make()
                    memory.writerow((k, method, "hot", peak_bytes(lambda: use(tables))))
            for method, operation in direct.items():
                if k <= memory_max or method == "NTT":
                    memory.writerow((k, method, "cold", peak_bytes(operation)))
            mem.flush()
            print(f"k={k}: {repetitions} замеров на метод", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--sizes", type=int, nargs="+", choices=SIZES, default=SIZES)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--extra-max", type=int, default=256, help="Последний порог для четырёх дорогих методов")
    parser.add_argument("--memory-max", type=int, default=512, help="Последний порог для замеров памяти классических методов")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("число повторов должно быть положительным")
    run(args.out, args.sizes, args.repeats, args.extra_max, args.memory_max)
