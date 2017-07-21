#load "Message.csx"

using System;
using System.Threading.Tasks;

using Microsoft.Bot.Builder.Azure;
using Microsoft.Bot.Builder.Dialogs; 
using Microsoft.Bot.Connector;
using Microsoft.Bot.Builder.ConnectorEx; 
using Microsoft.WindowsAzure.Storage; 
using Microsoft.WindowsAzure.Storage.Queue;   
using Microsoft.WindowsAzure.Storage.Blob;
using Newtonsoft.Json;
using System.Diagnostics;
using System.Net.Http.Headers;

[Serializable]
public class BasicProactiveEchoDialog : IDialog<object>
{
    public Task StartAsync(IDialogContext context)
    {
        context.Wait(MessageReceivedAsync);
        return Task.CompletedTask;
    }

    public virtual async Task MessageReceivedAsync(IDialogContext context, IAwaitable<IMessageActivity> argument)
    {
        var message = await argument;
        var shownMessage = $"Bing Image search results for '{message.Text}'";
        var atts = await GetAttachmentsAsync(message);
        foreach (byte[] attachmentData in atts) {
            message.Text = AddBlobAsync(attachmentData).Result;
            shownMessage = "your image";
        }
        // Create a queue Message
        var queueMessage = new Message
        {
            RelatesTo = context.Activity.ToConversationReference(),
            Text = message.Text
        };

        // write the queue Message to the queue
        await AddMessageToQueueAsync(JsonConvert.SerializeObject(queueMessage));

        await context.PostAsync($"Asking Raspberry to detect content on {shownMessage}");
        context.Wait(MessageReceivedAsync);
    }

    public static async Task AddMessageToQueueAsync(string message)
    {
        // Retrieve storage account from connection string.
        var storageAccount = CloudStorageAccount.Parse(Utils.GetAppSetting("AzureWebJobsStorage"));

        // Create the queue client.
        var queueClient = storageAccount.CreateCloudQueueClient();

        // Retrieve a reference to a queue.
        var queue = queueClient.GetQueueReference("rpi-queue");

        // Create the queue if it doesn't already exist.
        await queue.CreateIfNotExistsAsync();
        
        // Create a message and add it to the queue.
        var queuemessage = new CloudQueueMessage(message);
        await queue.AddMessageAsync(queuemessage); 
    }
    
    public static async Task<string> AddBlobAsync(byte[] data)
    { 
        // Retrieve storage account from connection string.
        var storageAccount = CloudStorageAccount.Parse(Utils.GetAppSetting("AzureWebJobsStorage"));

        // Create the blob client.
        CloudBlobClient blobClient = storageAccount.CreateCloudBlobClient();

        // Retrieve reference to a previously created container.
        CloudBlobContainer container = blobClient.GetContainerReference("botupload");
        container.CreateIfNotExists();

        // Retrieve reference to a new blob
        CloudBlockBlob blockBlob = container.GetBlockBlobReference(Guid.NewGuid().ToString());

        await blockBlob.UploadFromByteArrayAsync(data, 0, data.Length); 
 
        //Set the expiry time and permissions for the blob.
        //In this case, the start time is specified as a few minutes in the past, to mitigate clock skew.
        //The shared access signature will be valid immediately.
        SharedAccessBlobPolicy sasConstraints = new SharedAccessBlobPolicy();
        sasConstraints.SharedAccessStartTime = DateTimeOffset.UtcNow.AddMinutes(-5);
        sasConstraints.SharedAccessExpiryTime = DateTimeOffset.UtcNow.AddHours(24);
        sasConstraints.Permissions = SharedAccessBlobPermissions.Read;

        //Generate the shared access signature on the blob, setting the constraints directly on the signature.
        string sasBlobToken = blockBlob.GetSharedAccessSignature(sasConstraints);

        //Return the URI string for the container, including the SAS token.
        return blockBlob.Uri + sasBlobToken;
    }
    
    public static async Task<IEnumerable<byte[]>> GetAttachmentsAsync(IMessageActivity activity)
    {
        var attachments = activity?.Attachments?
            .Where(attachment => attachment.ContentUrl != null)
            .Select(c => Tuple.Create(c.ContentType, c.ContentUrl));
        var contentBytes = new List<byte[]>();
        if (attachments != null && attachments.Any())
        {
            using (var connectorClient = new ConnectorClient(new Uri(activity.ServiceUrl)))
            {
                var token = await (connectorClient.Credentials as MicrosoftAppCredentials).GetTokenAsync();
                foreach (var content in attachments)
                {
                    var uri = new Uri(content.Item2);
                    using (var httpClient = new HttpClient())
                    {
                        if (uri.Host.EndsWith("skype.com") && uri.Scheme == "https")
                        {
                            httpClient.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", token);
                            httpClient.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue("application/octet-stream"));
                        }
                        else
                        {
                            httpClient.DefaultRequestHeaders.Accept.Add(new MediaTypeWithQualityHeaderValue(content.Item1));
                        }
                        contentBytes.Add(await httpClient.GetByteArrayAsync(uri));
                    }
                }
            }
        }
        return contentBytes;
    }
}


