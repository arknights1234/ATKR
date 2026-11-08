# 명일방주 화면 번역기

명일방주 중국 서버 화면을 캡처해 PaddleOCR로 중국어를 읽고, 한국 서버 공식 번역이 있으면 그 번역을, 없으면 DeepL 번역을 게임 창 위에 표시합니다. [Windows](#사용법)와 [Android](#모바일-android)에서 쓸 수 있고, 같은 사전을 공유합니다.

## 사용법

1. 게임을 실행하고 `ArknightsScreenTranslator.exe`를 엽니다.
2. 게임 창을 선택하고 DeepL API 키를 입력한 뒤 **번역 시작**을 누릅니다. 키는 원하면 현재 Windows 계정 전용으로 암호화해 저장할 수 있습니다.
3. 단축키로 조작합니다.

| 단축키 | 동작 |
|---|---|
| Ctrl+S | 게임 창을 맨 앞으로 가져와 현재 화면을 번역합니다. 번역 중에는 정지 화면에 파란 테두리가 표시되고, 끝나면 결과로 바뀝니다. 결과가 떠 있을 때나 번역 중에 다시 누르면 닫습니다. |
| Ctrl+Shift+S | 오버레이를 종료합니다. |

사전이나 캐시에 있는 문구만 있는 화면은 DeepL API 키 없이도 번역됩니다. 글자 인식 모델은 배포본에 들어 있어 따로 내려받지 않습니다. 글자 인식에는 GPU를 쓰고(DirectML, VRAM 약 2 GB), GPU를 쓸 수 없으면 CPU로 전환합니다.

## 모바일 (Android)

게임을 하다가 스크린샷을 찍으면 그 화면을 번역해 전체 화면으로 띄워 주는 앱입니다.

1. [Releases](https://github.com/arknights1234/ATKR/releases)에서 APK를 받아 설치합니다. 스토어 앱이 아니므로 설치할 때 출처를 알 수 없는 앱을 허용해야 합니다.
2. 앱을 열고 DeepL API 키를 입력합니다. **API 키를 이 기기에 암호화해 저장**을 켜면 다음부터 다시 입력하지 않아도 됩니다.
3. 안내에 따라 사진, 알림, **다른 앱 위에 표시** 권한을 허용합니다.
4. **번역 시작**을 누르고 게임으로 돌아갑니다.
5. 볼륨 내리기 + 전원 버튼으로 스크린샷을 찍습니다. 잠시 뒤 번역된 화면이 뜨고, 터치하면 닫힙니다.

- 번역을 켜 둔 동안에는 다른 앱에서 찍은 스크린샷도 번역합니다. 쓰지 않을 때는 **번역 중지**나 알림의 종료를 누르세요.
- 시작 후 첫 번역은 모델을 불러오느라 조금 더 걸립니다. 글자 인식 모델은 앱에 들어 있어 따로 내려받지 않습니다.
- 찍은 스크린샷은 갤러리에 그대로 남습니다. 앱은 번역 결과를 파일로 저장하지 않습니다.
- 1.0.5부터 배포 APK의 서명이 바뀌었습니다. 1.0.4 이하를 설치했다면 앱을 한 번 삭제한 뒤 설치하세요. 삭제하면 저장한 API 키와 번역 캐시도 지워집니다.
- 사전 업데이트와 [DeepL 용어집](#deepl-용어집)은 Windows 앱과 같습니다. 용어집은 계정에 저장되므로 Windows에서 등록했다면 같은 API 키로 바로 적용됩니다.

## 번역 순서

1. 런타임 사전(`data/runtime_dictionary.sqlite3`)의 공식 번역. 공백, 전각 문자, 앞뒤 기호, 숫자가 달라도 찾습니다.
2. 이전 DeepL 결과 캐시(`deepl_translation_cache.json`).
3. DeepL API. 계정에 [DeepL 용어집](#deepl-용어집)이 있으면 용어집을 적용하고, 없으면 사전에 있는 고유명사를 공식 한국어로 고정해 보냅니다.

캡처 이미지와 OCR 결과는 메모리에서만 처리합니다. API 키는 코드나 파일에 평문으로 저장하지 않습니다.

## DeepL 용어집

사전에 없는 문구를 DeepL로 번역할 때 게임 용어와 이름을 공식 표기에 맞추는 용어집입니다. [data/glossary_arknights_glossary_v3.csv](data/glossary_arknights_glossary_v3.csv)에 중국어→한국어 용어 10,000개가 들어 있습니다. 등록하지 않아도 앱은 동작하지만, 등록하면 사전에 없는 문구의 용어 번역이 더 정확해집니다.

용어집은 DeepL 계정별로 저장되므로 각자 한 번 등록해야 합니다.

1. 앱에 DeepL API 키를 입력합니다.
2. **용어집 등록** 버튼을 누르고 확인을 누릅니다. 용어집이 API 키의 계정에 `arknights_glossary_v3`라는 이름으로 등록됩니다.
3. 번역 중이었다면 **번역 중지** 후 **번역 시작**을 다시 누릅니다.

같은 버튼으로 새 버전 용어집으로 바꿀 수 있고, 이름이 `arknights_glossary`로 시작하는 이전 용어집은 지워집니다. 용어집 등록은 번역 사용량에 포함되지 않습니다. DeepL 웹사이트에서 CSV를 가져오면 1,000개까지만 들어가므로 앱의 버튼을 쓰세요.

앱에는 따로 설정할 것이 없습니다. 번역할 때 API 키의 계정에서 이름이 `arknights_glossary`로 시작하는 중국어→한국어 용어집을 찾아 가장 최근 것을 사용합니다. 용어집을 찾지 못하거나 사용 중 오류가 나면 용어집 없이 번역합니다.

## 사전 업데이트

GUI의 **업데이트 확인**을 누르면 이 저장소 `main` 브랜치의 `data/runtime_dictionary.sqlite3`를 확인하고, 내용이 다르면 내려받아 검증한 뒤 적용합니다. Windows 앱과 Android 앱 모두 같은 파일을 받습니다. Android에서는 받을 때 약 65 MB의 데이터를 쓰고, 다음 번역부터 새 사전이 적용됩니다.

## 소스에서 실행

저장소 폴더를 가상환경으로 사용합니다.

```powershell
python -m venv .
.\Scripts\python.exe -m pip install -r requirements-ocr.txt
.\Scripts\python.exe translator_app.py
```

글자 인식 모델은 저장소의 `models` 폴더에 있습니다.

Android 앱은 [atkr-android](atkr-android) 폴더에 있습니다. JDK 17과 Android SDK(API 34)가 필요하고, 사전과 폰트는 빌드할 때 `data/`와 `font/`에서 가져옵니다.

```powershell
cd atkr-android
.\gradlew.bat assembleDebug
```

APK는 `app/build/outputs/apk/debug/ArknightsScreenTranslator.apk`에 만들어집니다.

배포용 APK는 `.\gradlew.bat assembleRelease`로 만들고 `app/build/outputs/apk/release/`에 생깁니다. 서명 키는 저장소에 없고, `atkr-android/local.properties`(커밋하지 않음)의 `atkr.signing=` 줄에 서명 정보 파일(`storeFile`, `storePassword`, `keyAlias`, `keyPassword`) 경로를 적으면 그 키로 서명합니다. 없으면 디버그 키로 서명합니다.

## 저작권 안내

이 프로젝트는 비공식 팬 프로젝트이며 Hypergryph, Yostar 등 명일방주(Arknights)의 권리자와 관련이 없습니다.

명일방주의 명칭, 게임 내 텍스트, 번역문을 비롯한 게임 관련 저작물의 권리는 각 권리자에게 있습니다. 런타임 사전과 DeepL 용어집에 포함된 게임 텍스트는 화면 번역 기능을 위한 비상업적 용도로만 사용합니다. 권리자의 요청이 있으면 해당 내용을 삭제합니다.

## 폰트와 모델 라이선스

번역 표시에 사용하는 Noto Sans CJK 폰트는 SIL Open Font License 1.1을 따릅니다. 라이선스 전문은 [font/LICENSE.txt](font/LICENSE.txt)에 있습니다.

글자 인식 모델은 [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)의 모델(Windows는 PP-OCRv6 medium, Android는 PP-OCRv5 mobile)을 ONNX 형식으로 변환한 것이며, Apache License 2.0을 따릅니다.
