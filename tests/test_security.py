import os
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, UploadFile

import app


class LimitesConfiguracionTests(unittest.TestCase):
    def test_acepta_limites_validos(self) -> None:
        resultado = app.validar_ajustes(
            {
                "top_k": 0,
                "top_p": 1,
                "temp": 0.1,
                "chars_por_bloque": 80,
            }
        )
        self.assertEqual(
            resultado,
            {
                "top_k": 0,
                "top_p": 1.0,
                "temp": 0.1,
                "chars_por_bloque": 80,
            },
        )

    def test_rechaza_valores_peligrosos(self) -> None:
        casos = (
            {"top_p": float("inf")},
            {"max_frames": 119},
            {"hilos": 2.5},
            {"binario": "/tmp/programa"},
            {"idioma": "xx"},
            {"dispositivo": "gpu con espacios"},
        )
        for datos in casos:
            with self.subTest(datos=datos), self.assertRaises(HTTPException):
                app.validar_ajustes(datos)


class AudioTests(unittest.IsolatedAsyncioTestCase):
    async def test_subida_se_detiene_al_superar_limite(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            destino = Path(temporal) / "audio.wav"
            subida = UploadFile(
                filename="audio.wav", file=tempfile.SpooledTemporaryFile()
            )
            subida.file.write(b"123456789")
            subida.file.seek(0)

            with patch.object(app, "MAX_BYTES_REFERENCIA", 8), self.assertRaises(
                HTTPException
            ) as contexto:
                await app.guardar_subida_limitada(subida, destino)

            self.assertEqual(contexto.exception.status_code, 413)
            self.assertLessEqual(destino.stat().st_size, app.TAMANO_CHUNK_SUBIDA)

    def test_sin_ffmpeg_solo_admite_wav_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            origen = Path(temporal) / "entrada.wav"
            destino = Path(temporal) / "salida.wav"
            with wave.open(str(origen), "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\x00\x00" * 160)

            with patch.object(app, "hay_ffmpeg", return_value=False):
                app.convertir_a_wav(origen, destino)

            self.assertTrue(destino.is_file())


class RetencionTests(unittest.TestCase):
    def test_elimina_archivos_caducados(self) -> None:
        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            antiguo = carpeta / "antiguo.wav"
            reciente = carpeta / "reciente.wav"
            antiguo.write_bytes(b"viejo")
            reciente.write_bytes(b"nuevo")
            os.utime(antiguo, (time.time() - 20, time.time() - 20))

            with patch.object(app, "DIR_SALIDAS", carpeta), patch.object(
                app, "RETENCION_SALIDAS_SEGUNDOS", 10
            ):
                app.limpiar_salidas()

            self.assertFalse(antiguo.exists())
            self.assertTrue(reciente.exists())


class TemporalesTests(unittest.TestCase):
    def test_elimina_parcial_si_el_proceso_falla(self) -> None:
        class ProcesoFallido:
            def __init__(self, cmd: list[str], **_kwargs: object) -> None:
                self.stdout = iter(())
                Path(cmd[cmd.index("-o") + 1]).write_bytes(b"parcial")

            def wait(self) -> int:
                return 1

        with tempfile.TemporaryDirectory() as temporal:
            carpeta = Path(temporal)
            tarea = app.Tarea("fallo", 1)
            cfg = dict(app.CONFIG_POR_DEFECTO)
            destino = carpeta / "resultado.wav"
            with patch.object(app, "DIR_TMP", carpeta), patch.object(
                app.subprocess, "Popen", ProcesoFallido
            ):
                app.ejecutar_sintesis(tarea, cfg, ["texto"], None, "", destino)

            self.assertEqual(tarea.estado, "error")
            self.assertEqual(list(carpeta.glob("fallo_*.wav")), [])


if __name__ == "__main__":
    unittest.main()
