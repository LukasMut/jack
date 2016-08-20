import itertools
import json
import random
import argparse

import tensorflow as tf

from quebap.projects.modelF.structs import FrozenIdentifier


class MultipleChoiceReader:
    """
    A MultipleChoiceReader reads and answers quebaps with multiple choice questions.
    It provides the interface between quebap files and tensorflow execution
    and optimisation: a batcher that converts quebaps into batched feed_dicts, a scoring TF node over
    answer candidates, and a training loss TF node.
    """

    def __init__(self, batcher, scores, loss):
        """

        :param batcher: batcher with a create_batches function (see AtomicBatcher)
        :param scores: [batch_size, num_candidates] TF matrix mapping each candidate in each batch to score
        :param loss: [batch_size] TF vector of per instance losses.
        """
        self.loss = loss
        self.scores = scores
        self.batcher = batcher


class AtomicBatcher:
    """
    This batcher wraps quebaps into placeholders:
    1. question_ids: A [batch_size] int vector where each component represents a single question using a single symbol.
    2. candidate_ids: A [batch_size, num_candidates] int matrix where each component represents a candidate answer using
    a single label.
    3. target_values: A [batch_size, num_candidates] float matrix representing the truth state of each candidate using
    1/0 values.
    """

    def __init__(self, reference_data):
        """
        Create a new atomic batcher.
        :param reference_data: the quebap dataset to use for initialising the question/candidate to id mapping.
        """
        self.reference_data = reference_data
        global_candidates = reference_data['globals']['candidates']
        all_candidates = set([c['text'] for c in global_candidates])
        instances = reference_data['instances']
        all_questions = set([inst['questions'][0]['question'] for inst in instances])
        self.question_lexicon = FrozenIdentifier(all_questions)
        self.candidate_lexicon = FrozenIdentifier(all_candidates)

        self.question_ids = tf.placeholder(tf.int32, (None,))
        self.candidate_ids = tf.placeholder(tf.int32, (None, None))
        self.target_values = tf.placeholder(tf.float32, (None, None))
        self.random = random.Random(0)
        self.num_candidates = len(self.candidate_lexicon)
        self.num_questions = len(self.question_lexicon)

    def create_batches(self, data=None, batch_size=1, test=False):
        """
        Creates a generator of batch feed_dicts. For training sets a single positive answer and a single negative
        answer are sampled for each question in the batch.
        :param data: data to convert into a generator of feed dicts, one per batch.
        :param batch_size: how large should each batch be.
        :param test: is this a training or test set.
        :return: a generator of batched feed_dicts.
        """
        instances = self.reference_data['instances'] if data is None else data['instances']
        for b in range(0, len(instances) // batch_size):
            batch = instances[b * batch_size: (b + 1) * batch_size]
            question_ids = [self.question_lexicon[inst['questions'][0]['question']]
                            for inst in batch]
            answer_ids = [self.candidate_lexicon[inst['questions'][0]['answers'][0]['text']]
                          for inst in batch]

            # sample negative candidate
            neg = [self.random.randint(0, len(self.candidate_lexicon) - 1) for _ in range(0, batch_size)]

            # todo: should go over all questions for same support
            yield {
                self.question_ids: question_ids,
                self.candidate_ids: [(pos, neg) for pos, neg in zip(answer_ids, neg)],
                self.target_values: [(1.0, 0.0) for _ in range(0, batch_size)]
            }


def create_dense_embedding(ids, repr_dim, num_symbols):
    """
    :param ids: tensor [d1, ... ,dn] of int32 symbols
    :param repr_dim: dimension of embeddings
    :param num_symbols: number of symbols
    :return: [d1, ... ,dn,repr_dim] tensor representation of symbols.
    """
    embeddings = tf.Variable(tf.random_normal((num_symbols, repr_dim)))
    encodings = tf.gather(embeddings, ids)  # [batch_size, repr_dim]
    return encodings


def create_dot_product_scorer(question_encodings, candidate_encodings):
    """

    :param question_encodings: [batch_size, enc_dim] tensor of question representations
    :param candidate_encodings: [batch_size, num_candidates, enc_dim] tensor of candidate encodings
    :return: a [batch_size, num_candidate] tensor of scores for each candidate
    """
    return tf.reduce_sum(tf.expand_dims(question_encodings, 1) * candidate_encodings, 2)


def create_softmax_loss(scores, target_values):
    """

    :param scores: [batch_size, num_candidates] logit scores
    :param target_values: [batch_size, num_candidates] vector of 0/1 target values.
    :return: [batch_size] vector of losses (or single number of total loss).
    """
    return tf.nn.softmax_cross_entropy_with_logits(scores, target_values)


def create_model_f_reader(reference_data, **options):
    """
    Create a ModelF reader.
    :param options: 'repr_dim', dimension of representation .
    :param reference_data: the data to determine the question / answer candidate symbols.
    :return: ModelF
    """
    batcher = AtomicBatcher(reference_data)
    question_encoder = create_dense_embedding(batcher.question_ids, options['repr_dim'], batcher.num_questions)
    candidate_encoder = create_dense_embedding(batcher.candidate_ids, options['repr_dim'], batcher.num_candidates)
    scores = create_dot_product_scorer(question_encoder, candidate_encoder)
    loss = create_softmax_loss(scores, batcher.target_values)
    return MultipleChoiceReader(batcher, scores, loss)


def train_reader(reader: MultipleChoiceReader, train_data, test_data, num_epochs, batch_size,
                 optimiser=tf.train.AdamOptimizer()):
    """
    Train a reader, and test on test set.
    :param reader: The reader to train
    :param train_data: the quebap training file
    :param test_data: the quebap test file
    :param num_epochs: number of epochs to train
    :param batch_size: size of each batch
    :param optimiser: the optimiser to use
    :return: Nothing
    """
    opt_op = optimiser.minimize(reader.loss)

    sess = tf.Session()
    sess.run(tf.initialize_all_variables())

    for epoch in range(0, num_epochs):
        for batch in reader.batcher.create_batches(train_data, batch_size=batch_size):
            _, loss = sess.run((opt_op, reader.loss), feed_dict=batch)

    # todo: also run dev during training
    for batch in reader.batcher.create_batches(test_data, test=True):
        scores = sess.run(reader.scores, feed_dict=batch)
        print(scores)
        # create


def main():
    readers = {
        'model_f': create_model_f_reader
    }

    parser = argparse.ArgumentParser(description='Train and Evaluate a machine reader')
    parser.add_argument('--train', type=argparse.FileType('r'), help="Quebap training file")
    parser.add_argument('--test', type=argparse.FileType('r'), help="Quebap test file")
    parser.add_argument('--batch_size', default=5, type=int)
    parser.add_argument('--repr_dim', default=5, type=int)
    parser.add_argument('--model', default='model_f', choices=sorted(readers.keys()))
    args = parser.parse_args()

    quebaps = json.load(args.train)

    reader = readers[args.model](quebaps, **vars(args))

    train_data = reader.batcher.create_batches(quebaps, args.batch_size)
    print(list(itertools.islice(train_data, 2)))

    sess = tf.Session()
    sess.run(tf.initialize_all_variables())

    feed_dict = next(train_data)
    print(sess.run(reader.scores, feed_dict=feed_dict))
    print(sess.run(reader.loss, feed_dict=feed_dict))


if __name__ == "__main__":
    main()