import unittest
import numpy as np
from evaluate_holdout import check_separation, evaluate


class Model:
    classes_ = ['ham', 'spam']

    def predict_proba(self, texts):
        # Test-only controlled probabilities, not measured accuracy.
        return np.array([[1 - {'spam delete': .9, 'spam notify': .7, 'ham delete': .8, 'ham allow': .1}[text],
                          {'spam delete': .9, 'spam notify': .7, 'ham delete': .8, 'ham allow': .1}[text]] for text in texts])


class HoldoutChecks(unittest.TestCase):
    def test_rejects_normalized_training_overlap(self):
        with self.assertRaises(ValueError):
            check_separation([('ham', '  Hello WORLD ')], [('ham', 'hello world'), ('spam', 'other')])

    def test_rejects_duplicate_and_single_class_holdouts(self):
        for rows in [[('ham', 'unique')], [('ham', 'same'), ('spam', ' SAME ')]]:
            with self.assertRaises(ValueError):
                check_separation([('spam', 'training')], rows)

    def test_policy_counts_false_deletion_and_strict_boundary(self):
        rows = [('spam', 'spam delete'), ('spam', 'spam notify'), ('ham', 'ham delete'), ('ham', 'ham allow')]
        check_separation([('ham', 'training')], rows)
        result = evaluate(Model(), rows)
        self.assertEqual(result['policy_decisions']['spam'], {'allow': 0, 'notify': 1, 'delete': 1})
        self.assertEqual(result['deletion_precision'], .5)
        self.assertEqual(result['spam_deletion_recall'], .5)
        self.assertEqual(result['ham_false_deletion_rate'], .5)


if __name__ == '__main__':
    unittest.main()
