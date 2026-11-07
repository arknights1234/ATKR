"""PP-OCRv6 medium on ONNX Runtime, using the GPU through DirectML when it can.

The steps and values are PaddleOCR's default text pipeline, so the app reads the same text as it did on Paddle.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import pyclipper

MODEL_DIR = Path(__file__).resolve().parent / "models"
GPU_PROVIDER = "DmlExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"

DETECT_STRIDE = 32
BINARY_THRESHOLD = 0.3
BOX_THRESHOLD = 0.6
UNCLIP_RATIO = 1.5
MAX_CANDIDATES = 3000
MIN_BOX_SIDE = 5
MIN_CROP_SIDE = 3
SAME_ROW_GAP = 10
READ_HEIGHT = 48
READ_MIN_WIDTH = 320
READ_MAX_WIDTH = 3200
READ_BATCH = 6
TALL_CROP_RATIO = 1.5
DETECT_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
DETECT_STD = np.array([0.229, 0.224, 0.225], np.float32)


def _open_session(path: Path, use_gpu: bool) -> ort.InferenceSession:
    options = ort.SessionOptions()
    options.log_severity_level = 3
    if use_gpu:
        # DirectML supports neither memory patterns nor parallel execution.
        options.enable_mem_pattern = False
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        return ort.InferenceSession(str(path), options, providers=[GPU_PROVIDER, CPU_PROVIDER])
    return ort.InferenceSession(str(path), options, providers=[CPU_PROVIDER])


def _detect_input_size(height: int, width: int) -> tuple[int, int]:
    return (
        max(round(height / DETECT_STRIDE) * DETECT_STRIDE, DETECT_STRIDE),
        max(round(width / DETECT_STRIDE) * DETECT_STRIDE, DETECT_STRIDE),
    )


def _box_score(probabilities: np.ndarray, contour: np.ndarray) -> float:
    height, width = probabilities.shape
    points = contour.reshape(-1, 2)
    left, right = np.clip(points[:, 0].min(), 0, width - 1), np.clip(points[:, 0].max(), 0, width - 1)
    top, bottom = np.clip(points[:, 1].min(), 0, height - 1), np.clip(points[:, 1].max(), 0, height - 1)
    mask = np.zeros((bottom - top + 1, right - left + 1), np.uint8)
    cv2.fillPoly(mask, [(points - [left, top]).astype(np.int32)], 1)
    return cv2.mean(probabilities[top:bottom + 1, left:right + 1], mask)[0]


def _unclip(contour: np.ndarray) -> np.ndarray | None:
    """Grow a region back to the glyph edges, since detection marks a shrunken text area."""
    length = cv2.arcLength(contour, True)
    if length == 0:
        return None
    offset = pyclipper.PyclipperOffset()
    offset.AddPath(contour.reshape(-1, 2).tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    paths = offset.Execute(abs(cv2.contourArea(contour)) * UNCLIP_RATIO / length)
    return np.array(paths[0], np.float32) if paths else None


def _clockwise(box: np.ndarray) -> np.ndarray:
    box = box[np.argsort(box[:, 0])]
    left = box[:2][np.argsort(box[:2, 1])]
    right = box[2:][np.argsort(box[2:, 1])]
    return np.array([left[0], right[0], right[1], left[1]], np.float32)


def _reading_order(boxes: list[np.ndarray]) -> list[np.ndarray]:
    """Top to bottom, then left to right among boxes on nearly the same row."""
    boxes = sorted(boxes, key=lambda box: (box[0][1], box[0][0]))
    for index in range(len(boxes) - 1):
        for other in range(index, -1, -1):
            if abs(boxes[other + 1][0][1] - boxes[other][0][1]) >= SAME_ROW_GAP or boxes[other + 1][0][0] >= boxes[other][0][0]:
                break
            boxes[other], boxes[other + 1] = boxes[other + 1], boxes[other]
    return boxes


def _crop(image: np.ndarray, box: np.ndarray) -> np.ndarray:
    width = max(1, int(max(np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3]))))
    height = max(1, int(max(np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2]))))
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], np.float32)
    transform = cv2.getPerspectiveTransform(box, target)
    crop = cv2.warpPerspective(image, transform, (width, height), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC)
    return np.rot90(crop) if height / width >= TALL_CROP_RATIO else crop


class OnnxOcr:
    def __init__(self, model_dir: Path = MODEL_DIR) -> None:
        self.model_dir = model_dir
        files = [model_dir / name for name in ("det.onnx", "rec.onnx", "rec_dict.txt")]
        missing = [str(path) for path in files if not path.exists()]
        if missing:
            raise RuntimeError(f"OCR 모델을 찾지 못했습니다: {', '.join(missing)}")
        # Index 0 is the CTC blank and the model's last class is a space.
        self.characters = ["", *files[2].read_text(encoding="utf-8").split("\n"), " "]
        self._open(use_gpu=GPU_PROVIDER in ort.get_available_providers())

    def _open(self, use_gpu: bool) -> None:
        try:
            self.detector = _open_session(self.model_dir / "det.onnx", use_gpu)
            self.reader = _open_session(self.model_dir / "rec.onnx", use_gpu)
        except Exception as error:  # ONNX Runtime raises its own types, which share no narrower base.
            if not use_gpu:
                raise
            print(f"GPU OCR을 시작하지 못해 CPU를 사용합니다: {error}", flush=True)
            self._open(use_gpu=False)
            return
        self.on_gpu = self.detector.get_providers()[0] == GPU_PROVIDER
        print(f"OCR 실행 장치: {'GPU (DirectML)' if self.on_gpu else 'CPU'}", flush=True)

    def recognize(self, image: np.ndarray) -> list[dict]:
        """Read an in-memory BGR image, falling back to the CPU if the GPU fails, as when out of memory."""
        try:
            return self._recognize(image)
        except Exception as error:  # ONNX Runtime raises its own types, which share no narrower base.
            if not self.on_gpu:
                raise
            print(f"GPU OCR 실패, CPU로 다시 시도합니다: {error}", flush=True)
            self._open(use_gpu=False)
            return self._recognize(image)

    def _recognize(self, image: np.ndarray) -> list[dict]:
        boxes = self._detect(image)
        texts = self._read_lines([_crop(image, box) for box in boxes])
        return [
            {"text": text.strip(), "score": score, "box": box.tolist()}
            for box, (text, score) in zip(boxes, texts)
            if text.strip()
        ]

    def warm_up(self, width: int, height: int) -> None:
        """Run both models once, since a GPU prepares each model on its first run."""
        input_height, input_width = _detect_input_size(height, width)
        self.detector.run(None, {self.detector.get_inputs()[0].name: np.zeros((1, 3, input_height, input_width), np.float32)})
        self.reader.run(None, {self.reader.get_inputs()[0].name: np.zeros((READ_BATCH, 3, READ_HEIGHT, READ_MIN_WIDTH), np.float32)})

    def _detect(self, image: np.ndarray) -> list[np.ndarray]:
        height, width = image.shape[:2]
        input_height, input_width = _detect_input_size(height, width)
        resized = image if (input_height, input_width) == (height, width) else cv2.resize(image, (input_width, input_height))
        pixels = ((resized.astype(np.float32) / 255.0 - DETECT_MEAN) / DETECT_STD).transpose(2, 0, 1)[None]
        probabilities = self.detector.run(None, {self.detector.get_inputs()[0].name: pixels})[0][0, 0]
        contours, _ = cv2.findContours((probabilities > BINARY_THRESHOLD).astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for contour in contours[:MAX_CANDIDATES]:
            if len(contour) < 3 or _box_score(probabilities, contour) < BOX_THRESHOLD:
                continue
            grown = _unclip(contour)
            if grown is None:
                continue
            rectangle = cv2.minAreaRect(grown)
            if min(rectangle[1]) < MIN_BOX_SIDE:
                continue
            box = _clockwise(cv2.boxPoints(rectangle))
            box[:, 0] = np.clip(np.round(box[:, 0] * width / input_width), 0, width - 1)
            box[:, 1] = np.clip(np.round(box[:, 1] * height / input_height), 0, height - 1)
            if np.linalg.norm(box[0] - box[1]) > MIN_CROP_SIDE and np.linalg.norm(box[0] - box[3]) > MIN_CROP_SIDE:
                boxes.append(box)
        return _reading_order(boxes)

    def _read_lines(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        results = [("", 0.0)] * len(crops)
        ratios = [crop.shape[1] / crop.shape[0] for crop in crops]
        # Lines of similar width share a batch, so the padding stays small.
        order = np.argsort(ratios)
        for start in range(0, len(order), READ_BATCH):
            batch = order[start:start + READ_BATCH]
            width = min(READ_MAX_WIDTH, max(READ_MIN_WIDTH, max(int(READ_HEIGHT * ratios[index]) for index in batch)))
            pixels = np.zeros((len(batch), 3, READ_HEIGHT, width), np.float32)
            for slot, index in enumerate(batch):
                used = min(width, max(1, math.ceil(READ_HEIGHT * ratios[index])))
                resized = cv2.resize(crops[index], (used, READ_HEIGHT)).astype(np.float32).transpose(2, 0, 1) / 255
                pixels[slot, :, :, :used] = (resized - 0.5) / 0.5
            output = self.reader.run(None, {self.reader.get_inputs()[0].name: pixels})[0]
            for slot, index in enumerate(batch):
                results[index] = self._decode(output[slot])
        return results

    def _decode(self, steps: np.ndarray) -> tuple[str, float]:
        """Greedy CTC: drop blanks and repeats."""
        ids = steps.argmax(1)
        keep = np.ones(len(ids), bool)
        keep[1:] = ids[1:] != ids[:-1]
        keep &= ids != 0
        kept = np.nonzero(keep)[0]
        if not len(kept):
            return "", 0.0
        return "".join(self.characters[ids[step]] for step in kept), float(steps[kept, ids[kept]].mean())
