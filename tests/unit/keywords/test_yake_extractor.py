"""Unit tests for YAKE JD keyword extraction + coverage scoring."""

from __future__ import annotations

from magicapply.domain.keywords.alignment import score_jd_keyword_coverage
from magicapply.domain.keywords.yake_extractor import YakeKeywordExtractor
from magicapply.domain.models.job import Job


def _job(description: str, title: str = "Data Scientist") -> Job:
    return Job.new(
        source_name="t",
        url="https://example.com/1",
        title=title,
        company="Acme",
        description=description,
        location="Remote",
    )


class TestYakeKeywordExtractor:
    def test_extracts_technical_terms_from_jd(self) -> None:
        jd = (
            "We are hiring a Data Scientist to build production machine learning "
            "pipelines using Python, PyTorch, SQL, and Kubernetes on AWS. "
            "Experience with RAG systems and large language models is required. "
            "You will design A/B tests and partner with product analytics."
        )
        terms = YakeKeywordExtractor(top=15).extract(_job(jd))
        assert terms, "expected YAKE to return keywords"
        lowered = {t.lower() for t in terms}
        # At least some real tech terms from the JD should surface.
        tech_hits = lowered & {
            "python",
            "pytorch",
            "sql",
            "kubernetes",
            "aws",
            "rag",
            "machine learning",
            "large language models",
            "data scientist",
        }
        assert tech_hits, f"no tech terms found in {terms}"

    def test_empty_description_returns_empty(self) -> None:
        terms = YakeKeywordExtractor().extract(_job(description=""))
        # Title alone may still yield something; pure empty job text → empty.
        empty = Job.new(
            source_name="t",
            url="https://example.com/2",
            title="",
            company="",
            description="",
            location=None,
        )
        assert YakeKeywordExtractor().extract(empty) == []

    def test_coverage_score_is_fraction_of_extracted_on_resume(self) -> None:
        jd = (
            "Must know Python and Kubernetes. Experience with Spark and Kafka "
            "for distributed data pipelines. Docker experience preferred."
        )
        terms = YakeKeywordExtractor(top=12).extract(_job(jd))
        assert len(terms) >= 2
        resume = (
            "Senior engineer. Skills: Python, Kubernetes, Docker. "
            "Built services with Python on Kubernetes."
        )
        result = score_jd_keyword_coverage(resume, terms)
        # Score is matched/total, not always 100 just because python is present.
        assert 0 <= result.value <= 100
        assert result.jd_terms
        assert len(result.matched) + len(result.missing) == len(result.jd_terms)
        expected = int(round(100 * len(result.matched) / len(result.jd_terms)))
        assert result.value == expected
