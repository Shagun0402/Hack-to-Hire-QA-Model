# -*- coding: utf-8 -*-
"""QA  Model v2.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1U7XHW2FKs0HzqsHm8pNLhfqElQiBoxSz
"""

# Commented out IPython magic to ensure Python compatibility.
# %%bash
# pip install rouge_score
# pip install torch datasets

import nltk
from nltk.corpus import stopwords
from nltk.tokenize import word_tokenize
from nltk.stem import PorterStemmer
from datasets import load_dataset
import re

# Download necessary NLTK data
nltk.download('punkt')
nltk.download('stopwords')

# Load the Quora Question Answer Dataset
dataset = load_dataset('toughdata/quora-question-answer-dataset')

# Initialize NLTK tools
stop_words = set(stopwords.words('english'))
ps = PorterStemmer()

def preprocess_text(text):
    # Remove irrelevant information (e.g., URLs, special characters)
    text = re.sub(r'http\S+|www\S+|https\S+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\@w+|\#','', text)
    text = re.sub(r'[^A-Za-z0-9 ]+', '', text)

    # Tokenization
    words = word_tokenize(text)

    # Stop word removal
    words = [word for word in words if word.lower() not in stop_words]

    # Stemming
    words = [ps.stem(word) for word in words]

    return ' '.join(words)

# Apply preprocessing to the dataset
dataset = dataset.map(lambda x: {'question': preprocess_text(x['question']), 'answer': preprocess_text(x['answer'])})

# Split the dataset into train and validation sets
dataset = dataset["train"].train_test_split(test_size=0.2)

print(dataset)

from transformers import AutoModelForQuestionAnswering, AutoTokenizer, TrainingArguments, Trainer, DefaultDataCollator
import torch
import numpy as np

model_names = ["t5-small", "bert-base-uncased", "gpt2"]

# Initialize models and tokenizers
models = {name: AutoModelForQuestionAnswering.from_pretrained(name) for name in model_names}
tokenizers = {name: AutoTokenizer.from_pretrained(name) for name in model_names}

# Add padding token for tokenizers that lack one
for name, tokenizer in tokenizers.items():
    if tokenizer.pad_token is None:
        tokenizer.add_special_tokens({'pad_token': tokenizer.eos_token})
        models[name].resize_token_embeddings(len(tokenizer))

# Define Data Processing Function
def add_token_positions(examples, tokenizer):
    questions = examples["question"]
    answers = examples["answer"]
    encodings = tokenizer(questions, truncation=True, padding="max_length", max_length=128)  # Reduced max_length

    start_positions = []
    end_positions = []

    for i in range(len(questions)):
        question = questions[i]
        answer = answers[i]

        start_position = question.find(answer)
        if start_position != -1:
            end_position = start_position + len(answer)
        else:
            start_position = 0
            end_position = 0

        start_positions.append(start_position if start_position < 128 else 0)
        end_positions.append(end_position if end_position < 128 else 0)

    return {
        "input_ids": encodings["input_ids"],
        "attention_mask": encodings["attention_mask"],
        "start_positions": start_positions,
        "end_positions": end_positions
    }
# Tokenize the datasets and add start/end positions
tokenized_datasets = {}
for name, tokenizer in tokenizers.items():
    tokenized_datasets[name] = dataset.map(lambda x: add_token_positions(x, tokenizer), batched=True)

print(tokenized_datasets)

# Define Training Arguments with Optimizations
training_args = TrainingArguments(
    output_dir="./results",
    evaluation_strategy="epoch",
    save_strategy="epoch",
    logging_dir="./logs",
    logging_steps=100,  # Increased logging steps for CPU
    per_device_train_batch_size=2,  # Reduced batch size
    per_device_eval_batch_size=2,   # Reduced batch size
    gradient_accumulation_steps=1,
    num_train_epochs=1,  # Reduced epochs for quicker runs
    report_to="none"
)

data_collator = DefaultDataCollator()

# Train and Save Models
trainers = {}
for name in model_names:
    trainer = Trainer(
        model=models[name],
        args=training_args,
        train_dataset=tokenized_datasets[name]["train"],
        eval_dataset=tokenized_datasets[name]["test"],
        tokenizer=tokenizers[name],
        data_collator=data_collator
    )
    trainers[name] = trainer
    trainer.train()
    trainer.save_model(f"./results/{name}")

    print(f"Training complete for {name}")

from datasets import load_metric

# Load metrics
f1_metric = load_metric('f1', trust_remote_code=True)
bleu_metric = load_metric('bleu', trust_remote_code=True)
rouge_metric = load_metric('rouge', trust_remote_code=True)

def compute_metrics(eval_pred):
    start_logits, end_logits, labels = eval_pred
    start_predictions = torch.argmax(start_logits, dim=-1)
    end_predictions = torch.argmax(end_logits, dim=-1)

    # Convert predictions to the format required by metrics
    predictions = {
        'start_positions': start_predictions.numpy().tolist(),
        'end_positions': end_predictions.numpy().tolist()
    }
    references = {
        'start_positions': labels['start_positions'].numpy().tolist(),
        'end_positions': labels['end_positions'].numpy().tolist()
    }

    f1 = f1_metric.compute(predictions=predictions, references=references)
    bleu = bleu_metric.compute(predictions=predictions, references=references)
    rouge = rouge_metric.compute(predictions=predictions, references=references)

    return {"f1": f1, "bleu": bleu, "rouge": rouge}

# Evaluate Models
evaluation_results = {}
for name, trainer in trainers.items():
    results = trainer.evaluate()
    eval_metrics = compute_metrics((results["start_logits"], results["end_logits"], results["labels"]))
    evaluation_results[name] = eval_metrics

print(evaluation_results)