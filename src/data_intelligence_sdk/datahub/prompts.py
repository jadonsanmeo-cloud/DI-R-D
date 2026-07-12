"""Prompts for datahub organization agents."""

from __future__ import annotations

import json
from dataclasses import asdict

from data_intelligence_sdk.core.types import DataCorpusPackage


class DataHubClusteringPrompt:
    """Builds chat messages for clustering a parsed datahub/corpus package."""

    def cluster_messages(
        self, corpus_package: DataCorpusPackage
    ) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {"corpus_package": asdict(corpus_package)},
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            },
        ]


_SYSTEM_PROMPT = """You are the DataHub Clustering agent for a data intelligence system.

Cluster the parsed datahub into semantic groups of related data assets. Use
source descriptions, schema descriptions, column names, catalog datasets, raw
files, vector collections, and relationship hints. Do not answer a user task
and do not select task-specific files.

DataHubClusteringResult JSON contract:
{
  "clusters": [
    {
      "cluster_id": "stable_snake_case_id",
      "name": "Human readable name",
      "description": "What this cluster covers",
      "members": [
        {
          "ref": "asset reference",
          "kind": "table | vector_collection | raw_file | source | document | other",
          "name": "optional asset name",
          "reason": "why this member belongs"
        }
      ],
      "relationships": ["short relationship notes"],
      "suggested_tasks": ["tasks this cluster supports"],
      "confidence": 0.0
    }
  ],
  "unclustered": [
    {
      "ref": "asset reference",
      "kind": "table | vector_collection | raw_file | source | document | other",
      "name": "optional asset name",
      "reason": "why it was left unclustered"
    }
  ],
  "notes": ["global clustering notes"]
}

Rules:
- Cluster by semantic/business relationship, not by storage backend alone.
- A vector collection may be its own retrieval cluster or a supporting member of
  a business cluster when its source documents clearly match that cluster.
- Include every important table, vector collection, raw file, or dataset when
  possible.
- Keep cluster_id stable, lowercase, and snake_case.
- Return only JSON.
"""
