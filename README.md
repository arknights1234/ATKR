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
3. DeepL API. 계정에 [DeepL 용어집](#deepl-용어집)이 있으면 용어집을 적용하고, 없으면 사전에 있는 고유명사를 공식 한국어로 고정해 보냅니다.

캡처 이미지와 OCR 결과는 메모리에서만 처리합니다. API 키는 코드나 파일에 평문으로 저장하지 않습니다.

## DeepL 용어집

사전에 없는 문구를 DeepL로 번역할 때 게임 용어와 이름을 공식 표기에 맞추는 용어집입니다. [data/glossary_arknights_glossary_v3.csv](data/glossary_arknights_glossary_v3.csv)에 중국어→한국어 용어 10,000개가 들어 있습니다. 등록하지 않아도 앱은 동작하지만, 등록하면 사전에 없는 문구의 용어 번역이 더 정확해집니다.

용어집은 DeepL 계정별로 저장되므로 각자 한 번 등록해야 합니다.

1. API 키를 발급받은 계정으로 [DeepL 웹사이트](https://www.deepl.com/)에 로그인합니다.
2. 용어집 메뉴에서 새 용어집을 만들고 위 CSV 파일을 가져옵니다.
3. 원문 언어를 중국어, 번역 언어를 한국어로 지정합니다.
4. 용어집 이름을 `arknights_glossary`로 시작하게 짓습니다. 예: `arknights_glossary_v3`
5. 앱이 실행 중이면 Ctrl+Shift+S로 오버레이를 종료한 뒤 **번역 시작**을 다시 누릅니다.

앱에는 따로 설정할 것이 없습니다. 번역할 때 API 키의 계정에서 이름이 `arknights_glossary`로 시작하는 중국어→한국어 용어집을 찾아 가장 최근 것을 사용합니다. 용어집을 찾지 못하거나 사용 중 오류가 나면 용어집 없이 번역합니다.

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

명일방주의 명칭, 게임 내 텍스트, 번역문을 비롯한 게임 관련 저작물의 권리는 각 권리자에게 있습니다. 런타임 사전과 DeepL 용어집에 포함된 게임 텍스트는 화면 번역 기능을 위한 비상업적 용도로만 사용합니다. 권리자의 요청이 있으면 해당 내용을 삭제합니다.

## 폰트 라이선스

번역 표시에 사용하는 Noto Sans CJK 폰트는 SIL Open Font License 1.1을 따릅니다. 라이선스 전문은 [font/LICENSE.txt](font/LICENSE.txt)에 있습니다.
