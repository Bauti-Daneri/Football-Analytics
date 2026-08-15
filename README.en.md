# Football Analytics

Tool for analyzing the sporting performance of football teams.

> 🇪🇸 Versión en español: [README.md](README.md)

## 🎯 Objective

Provide performance analysis for any football team. Load a team's data and get a diagnosis of its results, identifying patterns and factors associated with its performance.

## 🧠 Initial case study

To validate the tool, data from River Plate's season will be used, aiming to answer:

> **Why does River Plate keep losing its matches?**

The interest in this team is personal; the tool itself is designed to work with any team.

## 🏗 Project architecture

Complete system planned in phases. Each future phase will live in its own repository, which will be linked here as they are created. This repository corresponds to **Phase 1**.

- **Phase 1 — Data Analytics:** data collection, cleaning and analysis (this repo)
- **Phase 2 — Backend/API:** REST API serving the processed data *(repo to be created)*
- **Phase 3 — Web dashboard:** frontend consuming the API *(repo to be created)*
- **Phase 4 — Mobile:** Android app consuming the API *(repo to be created)*
- **Phase 5 — DevOps:** Docker + CI/CD on the above repositories *(repo to be created)*

## 🗂 Project structure

    data/raw/         Raw data
    data/processed/   Processed and cleaned data
    notebooks/        Exploratory analysis
    reports/figures/  Charts and visualizations
    src/              Source code

## 🛠 Stack

Python · pandas · Jupyter Notebook

## 🚀 Installation

*To be completed once the code is available.*

## 📈 Current status

In development — Phase 1 (Data Analytics) started.

## 🎓 Next steps

- Complete data collection and cleaning for the case study (River Plate)
- Pattern analysis and conclusions
- Phase 2: REST API with the processed data
- Phase 3: Web dashboard consuming the API
