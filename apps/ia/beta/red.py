"""Red neuronal multicapa (perceptrón multicapa) implementada desde cero con NumPy.

No usa bibliotecas de aprendizaje automático: la propagación hacia adelante, la retropropagación del error,
el optimizador Adam, la regularización L2, el dropout y la parada temprana están programados aquí.

Arquitectura: capas densas con activación ReLU en las capas ocultas y una salida
* sigmoide con entropía cruzada binaria (clasificación binaria), o
* softmax con entropía cruzada categórica (clasificación en varias clases).
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Historial:
    perdida: list[float] = field(default_factory=list)
    perdida_val: list[float] = field(default_factory=list)
    exactitud: list[float] = field(default_factory=list)
    exactitud_val: list[float] = field(default_factory=list)
    mejor_epoca: int = 0

    def como_dict(self):
        r = lambda xs: [round(float(x), 5) for x in xs]  # noqa: E731
        return {"perdida": r(self.perdida), "perdida_val": r(self.perdida_val), "exactitud": r(self.exactitud),
                "exactitud_val": r(self.exactitud_val), "mejor_epoca": self.mejor_epoca}


class RedNeuronal:
    def __init__(self, capas: list[int], salida: str = "sigmoide", dropout: float = 0.0, semilla: int = 42):
        if salida not in ("sigmoide", "softmax"):
            raise ValueError("salida debe ser 'sigmoide' o 'softmax'")
        if len(capas) < 2:
            raise ValueError("Se requieren al menos la capa de entrada y la de salida")
        self.capas = list(capas)
        self.salida = salida
        self.dropout = float(dropout)
        self.semilla = semilla
        rng = np.random.default_rng(semilla)
        # Inicialización de He: adecuada para ReLU.
        self.W = [rng.normal(0, np.sqrt(2.0 / capas[i]), (capas[i], capas[i + 1])) for i in range(len(capas) - 1)]
        self.b = [np.zeros((1, capas[i + 1])) for i in range(len(capas) - 1)]

    # ------------------------------------------------------------------ propagación
    @staticmethod
    def _relu(z):
        return np.maximum(0.0, z)

    @staticmethod
    def _sigmoide(z):
        return 1.0 / (1.0 + np.exp(-np.clip(z, -40, 40)))

    @staticmethod
    def _softmax(z):
        z = z - z.max(axis=1, keepdims=True)
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def _adelante(self, X, entrenando=False, rng=None):
        activaciones, mascaras = [X], []
        a = X
        for i in range(len(self.W)):
            z = a @ self.W[i] + self.b[i]
            ultima = i == len(self.W) - 1
            if ultima:
                a = self._sigmoide(z) if self.salida == "sigmoide" else self._softmax(z)
                mascaras.append(None)
            else:
                a = self._relu(z)
                if entrenando and self.dropout > 0:
                    m = (rng.random(a.shape) >= self.dropout) / (1.0 - self.dropout)  # dropout invertido
                    a = a * m
                    mascaras.append(m)
                else:
                    mascaras.append(None)
            activaciones.append(a)
        return activaciones, mascaras

    def predecir_proba(self, X) -> np.ndarray:
        X = np.atleast_2d(np.asarray(X, dtype=float))
        p = self._adelante(X)[0][-1]
        return p[:, 0] if self.salida == "sigmoide" else p

    # ------------------------------------------------------------------ pérdida
    def perdida(self, X, y, pesos_muestra=None, l2=0.0) -> float:
        p = self._adelante(np.asarray(X, dtype=float))[0][-1]
        return self._perdida(p, y, pesos_muestra, l2)

    def _perdida(self, p, y, pesos_muestra, l2):
        eps = 1e-9
        if self.salida == "sigmoide":
            y = y.reshape(-1, 1)
            ind = -(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))[:, 0]
        else:
            ind = -np.log(p[np.arange(len(y)), y] + eps)
        w = np.ones(len(ind)) if pesos_muestra is None else pesos_muestra
        datos = float((ind * w).sum() / w.sum())
        reg = 0.5 * l2 * sum(float((W ** 2).sum()) for W in self.W) / max(len(ind), 1)
        return datos + reg

    # ------------------------------------------------------------------ retropropagación
    def gradientes(self, X, y, pesos_muestra=None, l2=0.0, entrenando=False, rng=None):
        """Gradientes de la pérdida respecto a cada W y b (regla de la cadena, capa por capa)."""
        n = X.shape[0]
        acts, mascaras = self._adelante(X, entrenando, rng)
        p = acts[-1]
        w = (np.ones(n) if pesos_muestra is None else pesos_muestra).reshape(-1, 1)
        w = w / w.sum()
        # Con sigmoide + BCE y con softmax + entropía cruzada, dL/dz de la salida es (p - y).
        if self.salida == "sigmoide":
            delta = (p - y.reshape(-1, 1)) * w
        else:
            uno = np.zeros_like(p)
            uno[np.arange(n), y] = 1.0
            delta = (p - uno) * w
        gW, gb = [None] * len(self.W), [None] * len(self.W)
        for i in reversed(range(len(self.W))):
            gW[i] = acts[i].T @ delta + l2 * self.W[i] / n
            gb[i] = delta.sum(axis=0, keepdims=True)
            if i > 0:
                delta = delta @ self.W[i].T
                if mascaras[i - 1] is not None:
                    delta = delta * mascaras[i - 1]
                delta = delta * (acts[i] > 0)  # derivada de ReLU
        return gW, gb

    # ------------------------------------------------------------------ entrenamiento
    def entrenar(self, X, y, X_val=None, y_val=None, epocas=300, lote=32, tasa=0.01, l2=1e-3,
                 pesos_clase=None, paciencia=40, semilla=None, min_epocas=0) -> Historial:
        """Entrena con Adam por mini-lotes; si hay validación, aplica parada temprana y conserva los mejores pesos."""
        X = np.asarray(X, dtype=float)
        y = np.asarray(y).astype(float if self.salida == "sigmoide" else int)
        rng = np.random.default_rng(self.semilla if semilla is None else semilla)
        pm = None
        if pesos_clase is not None:
            pm = np.array([pesos_clase[int(c)] for c in y], dtype=float)
        mW = [np.zeros_like(W) for W in self.W]
        vW = [np.zeros_like(W) for W in self.W]
        mb = [np.zeros_like(b) for b in self.b]
        vb = [np.zeros_like(b) for b in self.b]
        b1, b2, eps, t = 0.9, 0.999, 1e-8, 0
        hist = Historial()
        mejor, mejores_pesos, sin_mejora = np.inf, None, 0
        for ep in range(epocas):
            orden = rng.permutation(len(X))
            for ini in range(0, len(X), lote):
                idx = orden[ini:ini + lote]
                gW, gb = self.gradientes(X[idx], y[idx], None if pm is None else pm[idx], l2, True, rng)
                t += 1
                for i in range(len(self.W)):
                    mW[i] = b1 * mW[i] + (1 - b1) * gW[i]
                    vW[i] = b2 * vW[i] + (1 - b2) * gW[i] ** 2
                    mb[i] = b1 * mb[i] + (1 - b1) * gb[i]
                    vb[i] = b2 * vb[i] + (1 - b2) * gb[i] ** 2
                    self.W[i] -= tasa * (mW[i] / (1 - b1 ** t)) / (np.sqrt(vW[i] / (1 - b2 ** t)) + eps)
                    self.b[i] -= tasa * (mb[i] / (1 - b1 ** t)) / (np.sqrt(vb[i] / (1 - b2 ** t)) + eps)
            hist.perdida.append(self.perdida(X, y, pm, l2))
            hist.exactitud.append(self.exactitud(X, y))
            if X_val is not None and len(X_val):
                pv = self.perdida(X_val, np.asarray(y_val), None, 0.0)
                hist.perdida_val.append(pv)
                hist.exactitud_val.append(self.exactitud(X_val, y_val))
                if ep < min_epocas:
                    continue
                if pv < mejor - 1e-5:
                    mejor, sin_mejora, hist.mejor_epoca = pv, 0, ep
                    mejores_pesos = ([W.copy() for W in self.W], [b.copy() for b in self.b])
                else:
                    sin_mejora += 1
                    if sin_mejora >= paciencia:
                        break
        if mejores_pesos is not None:
            self.W, self.b = mejores_pesos
        return hist

    def exactitud(self, X, y, umbral=0.5) -> float:
        p = self.predecir_proba(X)
        y = np.asarray(y)
        pred = (p >= umbral).astype(int) if self.salida == "sigmoide" else p.argmax(axis=1)
        return float((pred == y).mean()) if len(y) else 0.0

    # ------------------------------------------------------------------ persistencia
    def a_bytes(self, metadatos: dict | None = None) -> bytes:
        buf = io.BytesIO()
        arr = {f"W{i}": W for i, W in enumerate(self.W)} | {f"b{i}": b for i, b in enumerate(self.b)}
        meta = {"capas": self.capas, "salida": self.salida, "dropout": self.dropout, "semilla": self.semilla,
                "extra": metadatos or {}}
        np.savez_compressed(buf, meta=np.frombuffer(json.dumps(meta).encode(), dtype=np.uint8), **arr)
        return buf.getvalue()

    @classmethod
    def desde_bytes(cls, datos: bytes) -> tuple["RedNeuronal", dict]:
        z = np.load(io.BytesIO(bytes(datos)), allow_pickle=False)
        meta = json.loads(bytes(z["meta"]).decode())
        red = cls(meta["capas"], meta["salida"], meta["dropout"], meta["semilla"])
        red.W = [z[f"W{i}"] for i in range(len(red.W))]
        red.b = [z[f"b{i}"] for i in range(len(red.b))]
        return red, meta.get("extra", {})

    def num_parametros(self) -> int:
        return int(sum(W.size + b.size for W, b in zip(self.W, self.b, strict=True)))

    def describir(self) -> str:
        ocultas = " → ".join(str(c) for c in self.capas[1:-1])
        return f"{self.capas[0]} → {ocultas} → {self.capas[-1]} ({self.salida})"
