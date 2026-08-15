# Football Analytics

Herramienta de análisis del rendimiento deportivo de equipos de fútbol.

> 🇬🇧 English version: [README.en.md](README.en.md)

## 🎯 Objetivo

Brindar un análisis del rendimiento de cualquier equipo de fútbol. Permite cargar los datos de un equipo y obtener un diagnóstico de sus resultados, identificando patrones y factores asociados a su desempeño.

## 🧠 Caso de estudio inicial

Para validar la herramienta se usarán los datos de la temporada de River Plate, con el objetivo de responder:

> **¿Por qué River Plate sigue perdiendo sus partidos?**

El interés en este equipo es personal; la herramienta en sí está diseñada para funcionar con cualquier equipo.

## 🏗 Arquitectura del proyecto

Proyecto completo planificado por fases:

- **Fase 1 — Data Analytics:** recolección, limpieza y análisis de datos (este repo)
- **Fase 2 — Backend/API:** API REST que sirve los datos procesados
- **Fase 3 — Dashboard web:** frontend que consume la API
- **Fase 4 — Mobile:** app Android que consume la API
- **Fase 5 — DevOps:** Docker + CI/CD

## 🗂 Estructura del proyecto

    data/raw/         Datos crudos
    data/processed/   Datos procesados y limpios
    notebooks/        Análisis exploratorio
    reports/figures/  Gráficos y visualizaciones
    src/              Código fuente

## 🛠 Stack

Python · pandas · Jupyter Notebook

## 🚀 Instalación

*Se completa cuando el código esté disponible.*

## 📈 Estado actual

En desarrollo — Fase 1 (Data Analytics) iniciada.

## 🎓 Próximos pasos

- Completar recolección y limpieza de datos del caso de estudio (River Plate)
- Análisis de patrones y conclusiones
- Fase 2: API REST con los datos procesados
- Fase 3: Dashboard web que consuma la API
