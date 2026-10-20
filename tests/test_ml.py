import json
import os
import shutil
import subprocess
import tempfile
import unittest

try:
    import numpy as np
    import pandas as pd
    from ml.publish import publish
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


@unittest.skipIf(np is None or not shutil.which("php"), "numpy/pandas or php not installed")
class WebModelTest(unittest.TestCase):
    def test_web_computes_the_same_taste_as_python(self):
        rng = np.random.default_rng(1)
        films, users, k = 300, 50, 8
        model = {"film_ids": np.arange(1, films + 1), "film_vectors": rng.normal(size=(films, k)).astype(np.float32),
                 "film_bias": rng.normal(size=films).astype(np.float32), "film_counts": np.full(films, 100, np.int32),
                 "user_ids": np.arange(1, users + 1), "user_vectors": rng.normal(size=(users, k)).astype(np.float32),
                 "user_bias": np.zeros(users, np.float32), "mean": np.float32(3), "reg": np.float32(0.05),
                 "validation_rmse": np.float32(0.8)}
        kinds = pd.DataFrame({"kind": "film"}, index=pd.Index(model["film_ids"], name="film_id"))
        positions = rng.choice(films, 120, replace=False)
        stars = rng.integers(0, 6, 120).astype(np.float32)
        vector, bias = fold_in(model, positions, stars)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "model.bin")
            publish(model, kinds, pd.Series(300, index=model["user_ids"]), path, 1, 1, 0)
            script = (f"require '{root}/web/model.php'; $m = load_model('{path}'); "
                      "$r = []; foreach (json_decode(stream_get_contents(STDIN), true) as [$f, $s]) $r[$f] = $s; "
                      "echo json_encode(fold_in($m, $r));")
            ratings = [[int(model["film_ids"][p]), int(s)] for p, s in zip(positions, stars)]
            result = json.loads(subprocess.run(["php", "-r", script], input=json.dumps(ratings), text=True,
                                               capture_output=True, check=True).stdout)
        np.testing.assert_allclose(result["vector"], vector, atol=1e-4)
        self.assertAlmostEqual(result["bias"], float(bias), places=4)


if __name__ == "__main__":
    unittest.main()
