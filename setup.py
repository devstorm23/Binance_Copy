#!/usr/bin/env python3
"""
Setup script for Copy Trading Bot
"""

from setuptools import setup, find_packages
import os

# Read the README file
def read_readme():
    with open("README.md", "r", encoding="utf-8") as fh:
        return fh.read()

# Read requirements
def read_requirements():
    with open("requirements.txt", "r", encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="copy-trading-bot",
    version="1.0.0",
    author="Copy Trading Bot Team",
    author_email="support@copytradingbot.com",
    description="A comprehensive copy trading system for Binance futures trading",
    long_description=read_readme(),
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/copy-trading-bot",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Financial and Insurance Industry",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Office/Business :: Financial :: Investment",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=read_requirements(),
    extras_require={
        "dev": [
            "pytest>=6.0",
            "pytest-asyncio>=0.18.0",
            "black>=21.0",
            "flake8>=3.8",
            "mypy>=0.800",
        ],
        "docs": [
            "sphinx>=4.0",
            "sphinx-rtd-theme>=1.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "copy-trading-bot=main:main",
        ],
    },
    include_package_data=True,
    package_data={
        "": ["*.html", "*.css", "*.js", "*.txt", "*.md"],
    },
    keywords="trading, cryptocurrency, binance, copy-trading, futures, bot",
    project_urls={
        "Bug Reports": "https://github.com/yourusername/copy-trading-bot/issues",
        "Source": "https://github.com/yourusername/copy-trading-bot",
        "Documentation": "https://github.com/yourusername/copy-trading-bot#readme",
    },
)
