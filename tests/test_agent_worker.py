import unittest
from director.agent_worker import WorkerError, validate


def sample():
    return {"family_title": "Linear equation", "teaching_goal": "Model equality",
            "misconception": "Subtract on one side only",
            "problems": [{"a": 1, "b": i, "c": i + 3, "answer": 3,
                          "reasoning": "Subtract the constant from both sides",
                          "hint": "Keep both sides equal"} for i in range(8)]}


class CandidateValidation(unittest.TestCase):
    def test_valid(self):
        validate(sample())

    def test_bad_answer(self):
        x = sample()
        x["problems"][2]["answer"] = 4
        with self.assertRaises(WorkerError):
            validate(x)

    def test_duplicate(self):
        x = sample()
        x["problems"][1] = x["problems"][0].copy()
        with self.assertRaises(WorkerError):
            validate(x)

    def test_boolean_is_not_integer(self):
        x = sample()
        x["problems"][0]["answer"] = True
        with self.assertRaises(WorkerError):
            validate(x)


if __name__ == "__main__":
    unittest.main()
