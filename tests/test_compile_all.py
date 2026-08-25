"""تست کامپایل همه فایل‌های پایتون پروژه (تشخیص خطای سینتکس در CI)"""
import os
import glob
import py_compile


def test_all_python_files_compile():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    files = glob.glob(os.path.join(root, "*.py"))
    assert len(files) >= 10, "فایل‌های پروژه پیدا نشدند"
    for f in files:
        py_compile.compile(f, doraise=True)
