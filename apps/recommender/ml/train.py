"""Train the taste model.

Two stages, both offline. Nothing here is imported by the running site.

Stage 1 learns an embedding per anime from the community recommendation
graph. When a lot of people say "if you liked Steins;Gate, watch Erased",
those two titles are close in taste space, whatever their genre labels say.
We build the PPMI matrix of that graph and take its truncated SVD. That is
not a shortcut around word2vec: Levy and Goldberg (NIPS 2014) showed that
skip-gram with negative sampling is implicitly factorising a shifted PPMI
matrix, so the SVD reaches the same objective directly, deterministically,
and without a learning rate to babysit.

Stage 2 makes that space reachable from words. The embeddings only exist for
titles that were in the graph, but a viewer types "something dark and
psychological, and short". So we fit a ridge regression from TF-IDF content
features (genres, tags, description, era, length) onto the stage 1
embeddings. The result is a matrix that projects *any* bag of words into
taste space, including a questionnaire answer and a free-text wish.

What ships is that projection matrix, the IDF table, and one vector per title
in the ANIFLIX catalogue. Scoring is then a dot product, so the site needs
neither numpy nor this module at runtime.
"""

from __future__ import annotations

import math
import random
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from ..text import era_facet, length_facet, normalise_term, word_tokens
from .harvest import HarvestedTitle

# --------------------------------------------------------------------------
# Hyperparameters, chosen by sweeping them against `content_recall_at_10` —
# the end-to-end metric below, which asks whether describing a title lands you
# in the neighbourhood its fans actually watch.
#
# Worth knowing if you retune: the cosine and R² of the projection peak at a
# *lower* dimension than the ranking does. Optimising either of those picks
# the worse model, because a vector can be close on average and still order
# the top ten wrongly. Trust the recall numbers.
# --------------------------------------------------------------------------
EMBEDDING_DIMS = 96
VOCABULARY_SIZE = 14000
MIN_DOCUMENT_FREQUENCY = 3
RIDGE_LAMBDA = 2.0
PPMI_SHIFT = 1.0
MIN_EDGE_VOTES = 1
TEST_FRACTION = 0.1
RANDOM_SEED = 20260902

# Feature weights applied before TF-IDF. A genre label is a stronger
# statement about a title than a word that happens to be in its synopsis.
WEIGHT_GENRE = 3.0
WEIGHT_TAG = 2.0
WEIGHT_STUDIO = 1.0
WEIGHT_FACET = 1.5
WEIGHT_WORD = 1.0
# Title words are features too, which is what makes "something like Death
# Note" work: those tokens land on Death Note's own embedding, and the
# nearest catalogue titles to it are exactly the answer to that question.
WEIGHT_NAME = 2.5

# --------------------------------------------------------------------------
# Feature extraction
# --------------------------------------------------------------------------
def title_features(title: HarvestedTitle) -> Counter:
    """The weighted bag of terms that represents one anime."""
    features: Counter = Counter()

    for genre in title.genres:
        term = normalise_term("genre", genre)
        if term:
            features[term] += WEIGHT_GENRE

    for name, rank in title.tags:
        term = normalise_term("tag", name)
        if not term:
            continue
        # AniList ranks a tag 0-100 by how strongly voters feel it applies.
        # A 40%-rank tag is a whisper; a 90% one is what the show is about.
        if rank < 40:
            continue
        features[term] += WEIGHT_TAG * (rank / 100.0)

    for studio in title.studios:
        term = normalise_term("studio", studio)
        if term:
            features[term] += WEIGHT_STUDIO

    for facet in (era_facet(title.year), length_facet(title.episodes)):
        if facet:
            features[facet] += WEIGHT_FACET
    if title.format:
        features[normalise_term("format", title.format)] += WEIGHT_FACET

    for token in word_tokens(title.description):
        features[f"word:{token}"] += WEIGHT_WORD

    for name in (title.romaji, title.english):
        for token in word_tokens(name, boilerplate=False):
            features[f"word:{token}"] += WEIGHT_NAME

    return features


# --------------------------------------------------------------------------
# Stage 1: embeddings from the recommendation graph
# --------------------------------------------------------------------------
def build_cooccurrence(
    titles: list[HarvestedTitle], index: dict[int, int]
) -> np.ndarray:
    """Symmetric vote-weighted adjacency over titles present in the corpus.

    Votes are net scores, so a negative one means the crowd rejected the
    suggestion. Those are dropped rather than negated: a rejected pairing is
    weak evidence of dissimilarity, not strong evidence.
    """
    size = len(index)
    matrix = np.zeros((size, size), dtype=np.float32)

    for title in titles:
        row = index.get(title.anilist_id)
        if row is None:
            continue
        for target_id, votes in title.recommendations:
            column = index.get(target_id)
            if column is None or column == row or votes < MIN_EDGE_VOTES:
                continue
            # Vote counts are heavily skewed by popularity; the log keeps a
            # 3000-vote blockbuster pairing from drowning out everything else.
            weight = math.log1p(votes)
            matrix[row, column] += weight
            matrix[column, row] += weight

    return matrix


def ppmi(matrix: np.ndarray, *, shift: float = PPMI_SHIFT) -> np.ndarray:
    """Positive pointwise mutual information of the co-occurrence counts."""
    total = matrix.sum()
    if total <= 0:
        return matrix
    row_sums = matrix.sum(axis=1, keepdims=True)
    column_sums = matrix.sum(axis=0, keepdims=True)

    with np.errstate(divide="ignore", invalid="ignore"):
        expected = (row_sums @ column_sums) / total
        pmi = np.log((matrix / total) / np.where(expected > 0, expected / total, np.inf))

    pmi = np.nan_to_num(pmi, nan=0.0, neginf=0.0, posinf=0.0)
    return np.maximum(pmi - math.log(shift), 0.0).astype(np.float32)


def randomised_svd(
    matrix: np.ndarray, dims: int, *, oversample: int = 12, power_iterations: int = 4
) -> tuple[np.ndarray, np.ndarray]:
    """Top ``dims`` singular vectors, without factorising the whole matrix.

    A full SVD of a 10,000-title graph is a 10,000³ job and several gigabytes;
    we only ever want the first hundred or so directions. This is the standard
    randomised range finder (Halko, Martinsson and Tropp, 2011): project onto
    a small random subspace, sharpen it with a few power iterations because
    the PPMI spectrum decays slowly, orthonormalise, and factorise the small
    matrix that results.

    Accuracy is indistinguishable from the exact SVD at these ranks — the
    power iterations are what buy that — and it turns minutes into seconds.
    """
    size = matrix.shape[1]
    rank = min(size, dims + oversample)

    generator = np.random.default_rng(RANDOM_SEED)
    subspace = generator.standard_normal((size, rank)).astype(np.float32)

    sample = matrix @ subspace
    basis, _ = np.linalg.qr(sample)
    for _ in range(power_iterations):
        # Re-orthonormalising each half-step keeps float32 from collapsing the
        # basis onto the dominant direction.
        basis, _ = np.linalg.qr(matrix.T @ basis)
        basis, _ = np.linalg.qr(matrix @ basis)

    projected = basis.T @ matrix
    u_small, singular, _ = np.linalg.svd(projected, full_matrices=False)
    return (basis @ u_small)[:, :dims], singular[:dims]


def embed_items(matrix: np.ndarray, dims: int = EMBEDDING_DIMS) -> np.ndarray:
    """Truncated SVD of the PPMI matrix, rows L2-normalised.

    Singular values are folded in as sqrt, the standard symmetric split that
    keeps the dot product of two rows an approximation of their PPMI.
    """
    u, singular = randomised_svd(matrix, min(dims, min(matrix.shape)))
    embedding = u * np.sqrt(np.maximum(singular, 0.0))
    return l2_normalise(embedding)


def l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms > 0, norms, 1.0)


# --------------------------------------------------------------------------
# Stage 2: words -> taste space
# --------------------------------------------------------------------------
@dataclass
class Vocabulary:
    terms: list[str]
    idf: list[float]

    @property
    def position(self) -> dict[str, int]:
        return {term: i for i, term in enumerate(self.terms)}


def build_vocabulary(feature_rows: list[Counter], *, size: int = VOCABULARY_SIZE) -> Vocabulary:
    """Keep the terms that discriminate, drop the ones that only add bytes.

    Structured terms (genre, tag, facet) are kept ahead of description words:
    they are what the questionnaire speaks in, so a missing one is a question
    the model cannot hear.
    """
    document_frequency: Counter = Counter()
    for row in feature_rows:
        document_frequency.update(row.keys())

    eligible = [
        (term, count)
        for term, count in document_frequency.items()
        if count >= MIN_DOCUMENT_FREQUENCY
    ]
    structured = sorted(
        (item for item in eligible if not item[0].startswith("word:")),
        key=lambda item: (-item[1], item[0]),
    )
    words = sorted(
        (item for item in eligible if item[0].startswith("word:")),
        key=lambda item: (-item[1], item[0]),
    )

    chosen = structured + words[: max(0, size - len(structured))]
    total_documents = len(feature_rows)
    terms = [term for term, _ in chosen]
    idf = [
        math.log((1 + total_documents) / (1 + document_frequency[term])) + 1.0
        for term in terms
    ]
    return Vocabulary(terms=terms, idf=idf)


def vectorise(
    feature_rows: list[Counter], vocabulary: Vocabulary
) -> np.ndarray:
    """TF-IDF matrix, sublinear tf, L2-normalised rows."""
    position = vocabulary.position
    idf = np.asarray(vocabulary.idf, dtype=np.float32)
    matrix = np.zeros((len(feature_rows), len(vocabulary.terms)), dtype=np.float32)

    for row_index, row in enumerate(feature_rows):
        for term, weight in row.items():
            column = position.get(term)
            if column is not None:
                matrix[row_index, column] = (1.0 + math.log(weight)) if weight > 1 else weight

    matrix *= idf
    return l2_normalise(matrix)


def fit_projection(
    features: np.ndarray, targets: np.ndarray, *, ridge: float = RIDGE_LAMBDA
) -> np.ndarray:
    """Closed-form ridge regression from content features to taste space.

    ``W = (XᵀX + λI)⁻¹ XᵀY``. Solved rather than inverted, and in float64
    because the Gram matrix of a few thousand sparse-ish columns is
    ill-conditioned enough for float32 to matter.
    """
    x = features.astype(np.float64)
    y = targets.astype(np.float64)
    gram = x.T @ x
    gram[np.diag_indices_from(gram)] += ridge
    return np.linalg.solve(gram, x.T @ y).astype(np.float32)


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
def recall_at_k(
    embedding: np.ndarray,
    held_out: dict[int, set[int]],
    *,
    k: int = 10,
) -> float:
    """Of the edges we hid, how many come back as near neighbours?

    This is the honest question for stage 1: the model never saw these
    pairings, so hitting them means the geometry generalised.
    """
    if not held_out:
        return 0.0

    rows = sorted(held_out)
    similarity = embedding[rows] @ embedding.T
    for offset, row in enumerate(rows):
        similarity[offset, row] = -np.inf

    hits = 0
    total = 0
    top = np.argpartition(-similarity, kth=k, axis=1)[:, :k]
    for offset, row in enumerate(rows):
        neighbours = set(top[offset].tolist())
        wanted = held_out[row]
        hits += len(neighbours & wanted)
        total += len(wanted)
    return hits / total if total else 0.0


def content_recall_at_k(
    *,
    features: np.ndarray,
    weights: np.ndarray,
    embedding: np.ndarray,
    rows: list[int],
    neighbours: dict[int, set[int]],
    k: int = 10,
) -> float:
    """The metric that matches what the widget does.

    Take a title's content features, project them through the regression, and
    ask which titles come out nearest. If those are the titles the community
    actually recommends alongside it, then a viewer describing that same
    content in the questionnaire will land in the right neighbourhood.

    Cosine against the true embedding (``projection_fit``) only says the
    vector is roughly right. This says the *ranking* is right, which is the
    only thing a recommendation is judged on.
    """
    wanted = {row: neighbours.get(row, set()) for row in rows}
    wanted = {row: targets for row, targets in wanted.items() if targets}
    if not wanted:
        return 0.0

    ordered = sorted(wanted)
    predicted = l2_normalise(features[ordered] @ weights)
    similarity = predicted @ embedding.T
    for offset, row in enumerate(ordered):
        similarity[offset, row] = -np.inf

    top = np.argpartition(-similarity, kth=k, axis=1)[:, :k]
    hits = sum(
        len(set(top[offset].tolist()) & wanted[row]) for offset, row in enumerate(ordered)
    )
    total = sum(len(targets) for targets in wanted.values())
    return hits / total if total else 0.0


def projection_fit(
    features: np.ndarray, targets: np.ndarray, weights: np.ndarray
) -> tuple[float, float]:
    """Mean cosine of predicted vs true embedding, and R², on held-out rows."""
    predicted = l2_normalise(features @ weights)
    cosine = float(np.mean(np.sum(predicted * targets, axis=1)))
    residual = float(np.sum((features @ weights - targets) ** 2))
    variance = float(np.sum((targets - targets.mean(axis=0)) ** 2))
    r_squared = 1.0 - residual / variance if variance > 0 else 0.0
    return cosine, r_squared


# --------------------------------------------------------------------------
# The pipeline
# --------------------------------------------------------------------------
@dataclass
class TrainedModel:
    vocabulary: Vocabulary
    projection: np.ndarray            # (vocab, dims)
    item_embedding: np.ndarray        # (corpus titles, dims)
    corpus_ids: list[int]
    titles: dict[int, HarvestedTitle]
    metrics: dict[str, float]


def train(titles: list[HarvestedTitle], *, log=print) -> TrainedModel:
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    # Only titles that take part in the recommendation graph can get a stage 1
    # embedding, so the graph decides who is in the training set.
    linked: set[int] = set()
    by_id = {title.anilist_id: title for title in titles}
    for title in titles:
        for target_id, votes in title.recommendations:
            if votes >= MIN_EDGE_VOTES and target_id in by_id:
                linked.add(title.anilist_id)
                linked.add(target_id)

    corpus_ids = sorted(linked)
    index = {anilist_id: i for i, anilist_id in enumerate(corpus_ids)}
    log(f"  graph: {len(corpus_ids)} titles of {len(titles)} harvested")

    # Hold out a tenth of the edges before anything is fitted.
    held_out: dict[int, set[int]] = defaultdict(set)
    training_titles: list[HarvestedTitle] = []
    for title in titles:
        if title.anilist_id not in index:
            training_titles.append(title)
            continue
        kept: list[tuple[int, int]] = []
        for target_id, votes in title.recommendations:
            if target_id in index and votes >= MIN_EDGE_VOTES and random.random() < TEST_FRACTION:
                held_out[index[title.anilist_id]].add(index[target_id])
            else:
                kept.append((target_id, votes))
        clone = HarvestedTitle(**{**title.__dict__, "recommendations": kept})
        training_titles.append(clone)

    edges_held = sum(len(v) for v in held_out.values())
    log(f"  holding out {edges_held} edges for evaluation")

    cooccurrence = build_cooccurrence(training_titles, index)
    log(f"  co-occurrence: {int((cooccurrence > 0).sum())} non-zero cells")

    association = ppmi(cooccurrence)
    log(f"  fitting {EMBEDDING_DIMS}-dimension SVD over {association.shape[0]} titles…")
    embedding = embed_items(association)

    recall = recall_at_k(embedding, held_out, k=10)
    # A random ranker would score k / n. Reporting the ratio keeps the number
    # honest when the corpus size changes.
    baseline = 10 / max(len(corpus_ids), 1)
    log(f"  recall@10 on held-out edges: {recall:.3f}  (random baseline {baseline:.4f})")

    # Stage 2 trains only on titles that have an embedding to aim at.
    feature_rows = [title_features(by_id[anilist_id]) for anilist_id in corpus_ids]
    vocabulary = build_vocabulary(feature_rows)
    log(f"  vocabulary: {len(vocabulary.terms)} terms")

    features = vectorise(feature_rows, vocabulary)

    order = list(range(len(corpus_ids)))
    random.shuffle(order)
    split = int(len(order) * (1 - TEST_FRACTION))
    train_rows, test_rows = order[:split], order[split:]

    weights = fit_projection(features[train_rows], embedding[train_rows])
    cosine, r_squared = projection_fit(features[test_rows], embedding[test_rows], weights)
    log(f"  projection on held-out titles: cosine {cosine:.3f}, R² {r_squared:.3f}")

    # The end-to-end number: describe a title you have never trained on, and
    # see whether the model hands back the titles its fans actually watch.
    truth: dict[int, set[int]] = defaultdict(set)
    for title in titles:
        row = index.get(title.anilist_id)
        if row is None:
            continue
        for target_id, votes in title.recommendations:
            column = index.get(target_id)
            if column is not None and votes >= MIN_EDGE_VOTES:
                truth[row].add(column)

    content_recall = content_recall_at_k(
        features=features,
        weights=weights,
        embedding=embedding,
        rows=test_rows,
        neighbours=truth,
    )
    log(f"  description -> neighbours recall@10: {content_recall:.3f}")

    # Refit on everything now that the honest numbers are in hand.
    weights = fit_projection(features, embedding)

    return TrainedModel(
        vocabulary=vocabulary,
        projection=weights,
        item_embedding=embedding,
        corpus_ids=corpus_ids,
        titles=by_id,
        metrics={
            "recall_at_10": round(recall, 4),
            "recall_at_10_baseline": round(baseline, 5),
            "projection_cosine": round(cosine, 4),
            "projection_r2": round(r_squared, 4),
            "content_recall_at_10": round(content_recall, 4),
            "corpus_titles": len(titles),
            "graph_titles": len(corpus_ids),
            "held_out_edges": edges_held,
            "vocabulary": len(vocabulary.terms),
            "dimensions": EMBEDDING_DIMS,
        },
    )
