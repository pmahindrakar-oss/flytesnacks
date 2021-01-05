import typing

import matplotlib.pyplot as plt
import tensorflow as tf
import tensorflow_datasets as tfds
from flytekit import task, workflow
from flytekit.models.sagemaker import training_job as training_job_models
from flytekit.taskplugins.sagemaker import SagemakerTrainingJobConfig
from flytekit.types.file import FlyteFile


# %%
# Training Algorithm
# -------------------
# In this custom algorithm we will train MNIST using tensorflow.
#
# The trained model will be serialized using HDF5 encoding. Lets create a special type alias to denote this model
HDF5EncodedModelFile = FlyteFile[typing.TypeVar("hdf5")]

# %%
# The Training will produce 2 outputs
# 1. The serialized model in HDF5 format
# 2. And a log dictionary which is the Keras - `History.history`. This contains the accuracies and loss values
TrainingOutputs = typing.NamedTuple("TrainingOutputs", model=HDF5EncodedModelFile, epoch_logs=dict)


# %%
# Actual Algorithm
# ------------------
# To ensure that the code runs on Sagemaker, create a sagemaker task config using the class
# ``SagemakerTrainingJobConfig``
#
#  .. code::python
#
#       @task(
#        task_config=SagemakerTrainingJobConfig(
#         algorithm_specification=...,
#         training_job_resource_config=...,
#        )
def normalize_img(image, label):
    """Normalizes images: `uint8` -> `float32`."""
    return tf.cast(image, tf.float32) / 255., label


@task(
    task_config=SagemakerTrainingJobConfig(
        algorithm_specification=training_job_models.AlgorithmSpecification(
            input_mode=training_job_models.InputMode.FILE,
            algorithm_name=training_job_models.AlgorithmName.CUSTOM,
            algorithm_version="",
            input_content_type=training_job_models.InputContentType.TEXT_CSV,
        ),
        training_job_resource_config=training_job_models.TrainingJobResourceConfig(
            instance_type="ml.m4.xlarge",
            instance_count=1,
            volume_size_in_gb=25,
        ),
    ),
    cache_version="1.0",
    cache=True,
    container_image="{{.image.sagemaker-tf.fqn}}:{{.image.default.version}}"
)
def custom_training_task(epochs: int, batch_size: int) -> TrainingOutputs:
    (ds_train, ds_test), ds_info = tfds.load(
        'mnist',
        split=['train', 'test'],
        shuffle_files=True,
        as_supervised=True,
        with_info=True,
    )

    ds_train = ds_train.map(
        normalize_img, num_parallel_calls=tf.data.experimental.AUTOTUNE)
    ds_train = ds_train.cache()
    ds_train = ds_train.shuffle(ds_info.splits['train'].num_examples)
    ds_train = ds_train.batch(batch_size)
    ds_train = ds_train.prefetch(tf.data.experimental.AUTOTUNE)

    ds_test = ds_test.map(
        normalize_img, num_parallel_calls=tf.data.experimental.AUTOTUNE)
    ds_test = ds_test.batch(batch_size)
    ds_test = ds_test.cache()
    ds_test = ds_test.prefetch(tf.data.experimental.AUTOTUNE)

    model = tf.keras.models.Sequential([
        tf.keras.layers.Flatten(input_shape=(28, 28)),
        tf.keras.layers.Dense(128, activation='relu'),
        tf.keras.layers.Dense(10)
    ])
    model.compile(
        optimizer=tf.keras.optimizers.Adam(0.001),
        loss=tf.keras.losses.SparseCategoricalCrossentropy(from_logits=True),
        metrics=[tf.keras.metrics.SparseCategoricalAccuracy()],
    )

    history = model.fit(
        ds_train,
        epochs=epochs,
        validation_data=ds_test,
    )

    serialized_model = "my_model.h5"
    model.save(serialized_model, overwrite=True)

    return TrainingOutputs(model=HDF5EncodedModelFile(serialized_model), epoch_logs=history.history)


# %%
# Plot the metrics
# -----------------
# In the following task we will use the history logs from the training in the previous step and plot the curves using
# matplotlib. Images will be output as png.
PNGImage = FlyteFile[typing.TypeVar("png")]
PlotOutputs = typing.NamedTuple("PlotOutputs", accuracy=PNGImage, loss=PNGImage)


@task
def plot_loss_and_accuracy(epoch_logs: dict) -> PlotOutputs:
    # summarize history for accuracy
    plt.plot(epoch_logs['sparse_categorical_accuracy'])
    plt.plot(epoch_logs['val_sparse_categorical_accuracy'])
    plt.title('Sparse Categorical accuracy')
    plt.ylabel('accuracy')
    plt.xlabel('epoch')
    plt.legend(['train', 'test'], loc='upper left')
    accuracy_plot = "accuracy.png"
    plt.savefig(accuracy_plot)
    # summarize history for loss
    plt.plot(epoch_logs['loss'])
    plt.plot(epoch_logs['val_loss'])
    plt.title('model loss')
    plt.ylabel('loss')
    plt.xlabel('epoch')
    plt.legend(['train', 'test'], loc='upper left')
    loss_plot = "loss.png"
    plt.savefig(loss_plot)

    return PlotOutputs(accuracy=FlyteFile(accuracy_plot), loss=FlyteFile(loss_plot))


# %%
# The workflow takes in the hyperparams - in this case just the epochs and the batch_size and outputs the trained model
# and the plotted curves
@workflow
def mnist_trainer(epochs: int = 5, batch_size: int = 128) -> (HDF5EncodedModelFile, PNGImage, PNGImage):
    model, history = custom_training_task(epochs=epochs, batch_size=batch_size)
    accuracy, loss = plot_loss_and_accuracy(epoch_logs=history)
    return model, accuracy, loss


# %%
# As long as you have tensorflow setup locally, it will run like a regular python script
if __name__ == "__main__":
    print(mnist_trainer())