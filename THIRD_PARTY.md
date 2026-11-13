# Сторонние компоненты

ASCII Studio использует следующие проекты без изменения их исходного кода:

| Компонент | Версия | Проект |
| --- | --- | --- |
| CPython | 3.13 | https://www.python.org/ |
| PySide6 Essentials / Qt / Shiboken | 6.12.0 | https://www.qt.io/qt-for-python |
| NumPy | 2.5.4 | https://numpy.org/ |
| OpenCV Python headless | 5.0.0.93 | https://github.com/opencv/opencv-python |
| Pillow | 13.0.0 | https://python-pillow.github.io/ |
| imageio-ffmpeg | 0.8.1 | https://github.com/imageio/imageio-ffmpeg |
| FFmpeg | Сборка из imageio-ffmpeg | https://ffmpeg.org/ |
| PyInstaller | 6.22.3 | https://pyinstaller.org/ |

Тексты лицензий установленных компонентов и сведения о сборке FFmpeg сохраняются в `licenses/` при сборке. Qt и прочие библиотеки поставляются отдельно от исполняемого файла, внутри `_internal`.

Для интерфейса и ASCII-символов приложение читает системные шрифты Segoe UI и Consolas из Windows. Файлы этих шрифтов не включены в дистрибутив.

Демовидео создано процедурно в `ascii_studio/demo.py`; его простая звуковая дорожка синтезируется FFmpeg. Медиа из сторонних источников не используются.
