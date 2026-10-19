import unittest

try:
    import numpy as np
    from ml.recommend import cosine, fold_in
except ImportError:  # The ML dependencies are only installed on the training PC.
    np = None


@unittest.skipIf(np is None, "numpy/pandas not installed")
class FoldInTest(unittest.TestCase):
    def test_recovers_the_taste_vector_of_a_user(self):
        rng = np.random.default_rng(0)
        films = rng.normal(size=(300, 4)).astype(np.float32)
        taste = np.array([1.0, -0.5, 0.3, 0.0], np.float32)
        model = {"film_vectors": films, "film_bias": np.zeros(300, np.float32), "mean": np.float32(3), "reg": 0.0001}
        positions = np.arange(200)
        stars = 3 + 0.4 + films[positions] @ taste
        vector, bias = fold_in(model, positions, stars.astype(np.float32))
        np.testing.assert_allclose(vector, taste, atol=0.01)
        self.assertAlmostEqual(float(bias), 0.4, places=2)

    def test_cosine_ignores_length(self):
        vectors = np.array([[2, 0], [0, 3], [-1, 0]], np.float32)
        np.testing.assert_allclose(cosine(vectors, np.array([5, 0], np.float32)), [1, 0, -1])


if __name__ == "__main__":
    unittest.main()
