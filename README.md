# 명일방주 화면 번역기

명일방주 중국 서버 화면을 캡처해 PaddleOCR로 중국어를 읽고, 한국 서버 공식 번역이 있으면 그 번역을, 없으면 DeepL 번역을 게임 창 위에 표시하는 Windows 앱입니다.

## 사용법

1. 게임을 실행하고 `ArknightsScreenTranslator.exe`를 엽니다.
2. 게임 창을 선택하고 DeepL API 키를 입력한 뒤 **번역 시작**을 누릅니다. 키는 원하면 현재 Windows 계정 전용으로 암호화해 저장할 수 있습니다.
3. 단축키로 조작합니다.

| 단축키 | 동작 |
|---|---|
| Ctrl+S | 게임 창을 맨 앞으로 가져와 현재 화면을 번역합니다. 번역 중에는 정지 화면에 파란 테두리가 표시되고, 끝나면 결과로 바뀝니다. 결과가 떠 있을 때나 번역 중에 다시 누르면 닫습니다. |
| Ctrl+Shift+S | 오버레이를 종료합니다. |

사전이나 캐시에 있는 문구만 있는 화면은 DeepL API 키 없이도 번역됩니다. 처음 OCR을 실행할 때 모델(약 130 MB)을 한 번 내려받습니다.

## 번역 순서

1. 런타임 사전(`data/runtime_dictionary.sqlite3`)의 공식 번역. 공백, 전각 문자, 앞뒤 기호, 숫자가 달라도 찾습니다.
2. 이전 DeepL 결과 캐시(`deepl_translation_cache.json`).
3. DeepL API. 사전에 있는 고유명사는 공식 한국어로 고정해 보냅니다.

캡처 이미지와 OCR 결과는 메모리에서만 처리합니다. API 키는 코드나 파일에 평문으로 저장하지 않습니다.

## 사전 업데이트

GUI의 **업데이트 확인**을 누르면 이 저장소 `main` 브랜치의 `data/runtime_dictionary.sqlite3`를 확인하고, 내용이 다르면 내려받아 검증한 뒤 적용합니다.

## 소스에서 실행

저장소 폴더를 가상환경으로 사용합니다.

```powershell
python -m venv .
.\Scripts\python.exe -m pip install -r requirements-ocr.txt
.\Scripts\python.exe translator_app.py
```

## 저작권 안내

이 프로젝트는 비공식 팬 프로젝트이며 Hypergryph, Yostar 등 명일방주(Arknights)의 권리자와 관련이 없습니다.

명일방주의 명칭, 게임 내 텍스트, 번역문을 비롯한 게임 관련 저작물의 권리는 각 권리자에게 있습니다. 런타임 사전에 포함된 게임 텍스트는 화면 번역 기능을 위한 비상업적 용도로만 사용합니다. 권리자의 요청이 있으면 해당 내용을 삭제합니다.

## 폰트 라이선스

번역 표시에 사용하는 Noto Sans CJK 폰트는 SIL Open Font License 1.1을 따릅니다. 라이선스 전문은 [font/LICENSE.txt](font/LICENSE.txt)에 있습니다.
